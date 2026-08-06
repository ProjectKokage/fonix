#include "dort.h"

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CHECK(condition, message)                                             \
  do {                                                                        \
    if (!(condition)) {                                                       \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));   \
      return 1;                                                               \
    }                                                                         \
  } while (0)

#define LOAD_FUNCTION(handle, name, target)                                   \
  do {                                                                        \
    void* symbol = NULL;                                                      \
    dlerror();                                                                \
    symbol = dlsym((handle), (name));                                         \
    CHECK(dlerror() == NULL && symbol != NULL, "missing shim export");       \
    _Static_assert(                                                           \
        sizeof(target) == sizeof(symbol),                                     \
        "POSIX data and function pointers must have matching sizes");        \
    memcpy(&(target), &symbol, sizeof(symbol));                               \
  } while (0)

typedef dort_status_t* (DORT_CALL* runtime_open_function_t)(
    const dort_runtime_config_t*, dort_runtime_t**);
typedef void (DORT_CALL* runtime_release_function_t)(dort_runtime_t*);
typedef dort_status_t* (DORT_CALL* runtime_info_function_t)(
    const dort_runtime_t*, dort_string_t*);
typedef void (DORT_CALL* string_release_function_t)(dort_string_t*);
typedef uint32_t (DORT_CALL* status_domain_function_t)(const dort_status_t*);
typedef int32_t (DORT_CALL* status_code_function_t)(const dort_status_t*);
typedef const char* (DORT_CALL* status_message_function_t)(
    const dort_status_t*);
typedef void (DORT_CALL* status_release_function_t)(dort_status_t*);

static dort_runtime_config_t bundled_config(void) {
  dort_runtime_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_RUNTIME_CONFIG_V1_SIZE;
  config.shim_abi_version = DORT_ABI_VERSION;
  config.required_ort_api_version = DORT_ORT_API_COMPATIBILITY_FLOOR;
  config.source_kind = DORT_RUNTIME_SOURCE_BUNDLED;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "flutter-framework-runtime-test";
  return config;
}

int main(int argc, char** argv) {
  void* shim = NULL;
  runtime_open_function_t runtime_open = NULL;
  runtime_release_function_t runtime_release = NULL;
  runtime_info_function_t runtime_info = NULL;
  string_release_function_t string_release = NULL;
  status_domain_function_t status_domain = NULL;
  status_code_function_t status_code = NULL;
  status_message_function_t status_message = NULL;
  status_release_function_t status_release = NULL;
  dort_runtime_config_t config = bundled_config();
  dort_runtime_t* runtime = NULL;
  dort_status_t* status = NULL;
  dort_string_t info;
  const int expect_success = argc == 3 && strcmp(argv[2], "success") == 0;

  CHECK(argc == 3, "expected a shim path and success/reject mode");
  CHECK(
      expect_success || strcmp(argv[2], "reject") == 0,
      "expected success or reject mode");
  dlerror();
  shim = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  CHECK(dlerror() == NULL && shim != NULL, "could not load the copied shim");
  LOAD_FUNCTION(shim, "dort_runtime_open", runtime_open);
  LOAD_FUNCTION(shim, "dort_runtime_release", runtime_release);
  LOAD_FUNCTION(shim, "dort_runtime_info_json", runtime_info);
  LOAD_FUNCTION(shim, "dort_string_release", string_release);
  LOAD_FUNCTION(shim, "dort_status_domain", status_domain);
  LOAD_FUNCTION(shim, "dort_status_code", status_code);
  LOAD_FUNCTION(shim, "dort_status_message", status_message);
  LOAD_FUNCTION(shim, "dort_status_release", status_release);

  status = runtime_open(&config, &runtime);
  if (!expect_success) {
    CHECK(runtime == NULL, "unexpected layout returned a runtime");
    CHECK(status != NULL, "unexpected layout returned no status");
    CHECK(
        status_domain(status) == DORT_ERROR_DOMAIN_LOADER,
        "unexpected layout returned the wrong status domain");
    CHECK(
        status_code(status) == DORT_ERROR_PLATFORM,
        "unexpected layout returned the wrong status code");
    CHECK(
        strstr(status_message(status), "exact Flutter macOS framework layout") !=
            NULL,
        "unexpected layout returned the wrong status message");
    status_release(status);
    CHECK(dlclose(shim) == 0, "could not close rejected copied shim");
    puts("Fonix rejected an unexpected macOS bundle layout.");
    return 0;
  }

  if (status != NULL) {
    fprintf(stderr, "FAIL framework bundled open: %s\n", status_message(status));
    status_release(status);
    (void)dlclose(shim);
    return 1;
  }
  CHECK(runtime != NULL, "framework bundled open returned a null runtime");
  memset(&info, 0, sizeof(info));
  status = runtime_info(runtime, &info);
  CHECK(status == NULL, "framework bundled runtime information failed");
  CHECK(info.data != NULL, "framework bundled runtime information is empty");
  CHECK(
      strstr((const char*)info.data, "1.27.99-fonix-test") != NULL,
      "framework fake runtime version was not negotiated");
  string_release(&info);
  runtime_release(runtime);
  CHECK(dlclose(shim) == 0, "could not close successful copied shim");
  puts("Fonix Flutter macOS framework runtime test passed.");
  return 0;
}
