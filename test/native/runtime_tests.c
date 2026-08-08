#include "dort.h"

#include <dlfcn.h>
#include <pthread.h>
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

static dort_runtime_config_t base_config(uint32_t source_kind) {
  dort_runtime_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_RUNTIME_CONFIG_V1_SIZE;
  config.shim_abi_version = DORT_ABI_VERSION;
  config.required_ort_api_version = DORT_ORT_API_COMPATIBILITY_FLOOR;
  config.source_kind = source_kind;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "fonix-native-test";
  return config;
}

static int expect_error(
    dort_status_t* status,
    uint32_t domain,
    int32_t code,
    const char* operation_fragment,
    const char* message_fragment) {
  CHECK(status != NULL, "expected a native error status");
  CHECK(dort_status_domain(status) == domain, "unexpected error domain");
  CHECK(dort_status_code(status) == code, "unexpected error code");
  CHECK(
      strstr(dort_status_operation(status), operation_fragment) != NULL,
      "unexpected status operation");
  CHECK(
      strstr(dort_status_message(status), message_fragment) != NULL,
      "unexpected status message");
  dort_status_release(status);
  return 0;
}

static int test_build_contract(void) {
  dort_string_t manifest;
  dort_status_t* status = NULL;
  memset(&manifest, 0, sizeof(manifest));

  CHECK(dort_get_abi_version() == 1u, "wrong shim ABI version");
  CHECK(
      dort_get_ort_api_compatibility_floor() == 27u,
      "wrong ORT API compatibility floor");
  status = dort_get_build_manifest_json(&manifest);
  CHECK(status == NULL, "build manifest failed");
  CHECK(manifest.struct_size == sizeof(manifest), "wrong string struct size");
  CHECK(manifest.data != NULL, "missing build manifest data");
  CHECK(
      strstr((const char*)manifest.data, "\"nativeIdentity\":\"fonix_shim\"") !=
          NULL,
      "wrong native identity");
  CHECK(
      strstr((const char*)manifest.data, "\"schemaVersion\":3") != NULL,
      "wrong build manifest schema");
  CHECK(
      strstr((const char*)manifest.data, "\"runtimeProfile\":\"external\"") !=
          NULL,
      "wrong runtime profile");
  CHECK(
      strstr((const char*)manifest.data, "\"androidRuntimeOwner\":null") !=
          NULL,
      "unexpected Android runtime owner");
  CHECK(
      strstr(
          (const char*)manifest.data,
          "\"allowedRuntimeSources\":[\"process\",\"file\"]") != NULL,
      "wrong runtime source policy");
  CHECK(
      strstr((const char*)manifest.data, "\"artifact\":null") != NULL,
      "external native test unexpectedly reported an artifact");
  dort_string_release(&manifest);
  CHECK(manifest.data == NULL && manifest.private_owner == NULL, "string release did not clear fields");
  dort_string_release(&manifest);
  dort_string_release(NULL);
  status = dort_get_build_manifest_json(NULL);
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, "string_copy", "null") == 0, "null build-manifest output assertion failed");
  CHECK(dort_status_domain(NULL) == DORT_ERROR_DOMAIN_NONE, "null status domain was not neutral");
  CHECK(dort_status_code(NULL) == DORT_ERROR_NONE, "null status code was not neutral");
  CHECK(strcmp(dort_status_message(NULL), "") == 0, "null status message was not empty");
  dort_status_release(NULL);
  memset(&manifest, 0xff, sizeof(manifest));
  status = dort_runtime_info_json(NULL, &manifest);
  CHECK(manifest.data == NULL && manifest.private_owner == NULL, "runtime-info failure did not clear output");
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, "runtime_info", "invalid") == 0, "invalid runtime-info assertion failed");
  dort_runtime_retain(NULL);
  dort_runtime_release(NULL);
  return 0;
}

static int test_closed_config_validation(const char* good_path) {
  dort_runtime_t* runtime = (dort_runtime_t*)(uintptr_t)1u;
  dort_runtime_config_t config = base_config(DORT_RUNTIME_SOURCE_FILE);
  dort_status_t* status = NULL;

  config.library_path_utf8 = good_path;
  status = dort_runtime_open(&config, NULL);
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, "runtime_open", "output runtime") == 0, "null runtime output assertion failed");

  config.shim_abi_version = 2u;
  status = dort_runtime_open(&config, &runtime);
  CHECK(runtime == NULL, "ABI mismatch did not initialize output to null");
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_ABI_MISMATCH, "runtime_open", "ABI") == 0, "ABI mismatch assertion failed");

  config = base_config(DORT_RUNTIME_SOURCE_FILE);
  config.library_path_utf8 = good_path;
  config.required_ort_api_version = 28u;
  status = dort_runtime_open(&config, &runtime);
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_ORT_API, DORT_ERROR_API_REQUEST_UNSUPPORTED, "runtime_open", "exactly") == 0, "API request assertion failed");

  config = base_config(DORT_RUNTIME_SOURCE_BUNDLED);
  config.library_path_utf8 = good_path;
  status = dort_runtime_open(&config, &runtime);
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_UNSUPPORTED, DORT_ERROR_SOURCE_UNSUPPORTED, "runtime_open", "external") == 0, "profile/source assertion failed");

  config = base_config(DORT_RUNTIME_SOURCE_FILE);
  config.library_path_utf8 = "relative/libonnxruntime.dylib";
  status = dort_runtime_open(&config, &runtime);
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PATH_NOT_ABSOLUTE, "runtime_open", "absolute") == 0, "relative path assertion failed");

  config = base_config(DORT_RUNTIME_SOURCE_PROCESS);
  config.preferred_library_name_count = 1u;
  config.preferred_library_names_utf8 = NULL;
  status = dort_runtime_open(&config, &runtime);
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, "runtime_open", "pointer/count") == 0, "name pointer/count assertion failed");

  config = base_config(DORT_RUNTIME_SOURCE_FILE);
  config.library_path_utf8 = good_path;
  config.log_id_utf8 = "\xc0\xaf";
  status = dort_runtime_open(&config, &runtime);
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_UTF8, "runtime_open", "UTF-8") == 0, "invalid UTF-8 assertion failed");
  return 0;
}

static int test_process_runtime(const char* good_path) {
  void* preload = NULL;
  dort_runtime_config_t config = base_config(DORT_RUNTIME_SOURCE_PROCESS);
  dort_runtime_t* runtime = NULL;
  dort_string_t info;
  dort_status_t* status = NULL;

  preload = dlopen(good_path, RTLD_NOW | RTLD_GLOBAL);
  CHECK(preload != NULL, dlerror() == NULL ? "could not preload fake ORT" : dlerror());
  status = dort_runtime_open(&config, &runtime);
  CHECK(status == NULL, status == NULL ? "process open failed" : dort_status_message(status));
  CHECK(runtime != NULL, "process open returned null runtime");
  memset(&info, 0, sizeof(info));
  status = dort_runtime_info_json(runtime, &info);
  CHECK(status == NULL, "process runtime info failed");
  CHECK(strstr((const char*)info.data, "\"runtimeSource\":\"process\"") != NULL, "process source missing from diagnostics");
  CHECK(strstr((const char*)info.data, "1.27.99-fonix-test") != NULL, "runtime version missing from diagnostics");
  dort_string_release(&info);

  dort_runtime_retain(runtime);
  dort_runtime_release(runtime);
  memset(&info, 0, sizeof(info));
  status = dort_runtime_info_json(runtime, &info);
  CHECK(status == NULL, "retained runtime became invalid");
  dort_string_release(&info);
  dort_runtime_release(runtime);

  /* The shim retains the successfully negotiated loader identity for process lifetime. */
  dlclose(preload);
  return 0;
}

static int test_file_runtime(const char* good_path, const char* allowed_root) {
  dort_runtime_config_t config = base_config(DORT_RUNTIME_SOURCE_FILE);
  dort_runtime_t* runtime = NULL;
  dort_string_t info;
  dort_status_t* status = NULL;

  config.library_path_utf8 = good_path;
  config.allowed_root_utf8 = allowed_root;
  status = dort_runtime_open(&config, &runtime);
  CHECK(status == NULL, status == NULL ? "file open failed" : dort_status_message(status));
  CHECK(runtime != NULL, "file open returned null runtime");
  memset(&info, 0, sizeof(info));
  status = dort_runtime_info_json(runtime, &info);
  CHECK(status == NULL, "file runtime info failed");
  CHECK(strstr((const char*)info.data, "\"runtimeSource\":\"file\"") != NULL, "file source missing from diagnostics");
  CHECK(strstr((const char*)info.data, "\"logSeverity\":\"warning\"") != NULL, "log severity missing from diagnostics");
  CHECK(strstr((const char*)info.data, "fonix-native-test") != NULL, "log ID missing from diagnostics");
  CHECK(
      strstr((const char*)info.data, allowed_root) == NULL,
      "runtime diagnostics exposed a private parent path");
  CHECK(
      strstr((const char*)info.data, strrchr(good_path, '/') + 1) != NULL,
      "runtime diagnostics omitted the redacted library basename");
  dort_string_release(&info);

  memset(&info, 0, sizeof(info));
  status = dort_runtime_available_providers_json(runtime, &info);
  CHECK(status == NULL, "provider discovery failed");
  CHECK(
      strstr((const char*)info.data, "\"schemaVersion\":1") != NULL,
      "provider discovery schema is missing");
  CHECK(
      strstr((const char*)info.data, "CPUExecutionProvider") != NULL &&
          strstr((const char*)info.data, "CoreMLExecutionProvider") != NULL,
      "provider discovery omitted a fake provider");
  dort_string_release(&info);
  dort_runtime_release(runtime);
  return 0;
}

static int test_provider_discovery_failure(
    const char* bad_path,
    const char* allowed_root) {
  dort_runtime_config_t config = base_config(DORT_RUNTIME_SOURCE_FILE);
  dort_runtime_t* runtime = NULL;
  dort_string_t discovery;
  dort_status_t* status = NULL;

  memset(&discovery, 0xff, sizeof(discovery));
  status = dort_runtime_available_providers_json(NULL, &discovery);
  CHECK(
      discovery.struct_size == (uint32_t)sizeof(discovery) &&
          discovery.data == NULL && discovery.length == 0u &&
          discovery.private_owner == NULL,
      "invalid-runtime provider discovery did not clear its output");
  CHECK(
      expect_error(
          status,
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          "runtime_available_providers_json",
          "invalid") == 0,
      "invalid-runtime provider-discovery assertion failed");

  config.library_path_utf8 = bad_path;
  config.allowed_root_utf8 = allowed_root;
  status = dort_runtime_open(&config, &runtime);
  CHECK(status == NULL, "bad-discovery fake did not open");
  CHECK(runtime != NULL, "bad-discovery fake returned a null runtime");
  memset(&discovery, 0xff, sizeof(discovery));
  status = dort_runtime_available_providers_json(runtime, &discovery);
  CHECK(
      discovery.struct_size == (uint32_t)sizeof(discovery) &&
          discovery.data == NULL && discovery.length == 0u &&
          discovery.private_owner == NULL,
      "provider-discovery failure did not clear its output");
  CHECK(
      expect_error(
          status,
          DORT_ERROR_DOMAIN_ORT_API,
          DORT_ERROR_LIMIT_EXCEEDED,
          "runtime_available_providers_json",
          "oversized") == 0,
      "oversized provider-discovery assertion failed");
  dort_runtime_release(runtime);
  return 0;
}

static int test_provider_partial_owner_cleanup(
    const char* partial_path,
    const char* allowed_root) {
  dort_runtime_config_t config = base_config(DORT_RUNTIME_SOURCE_FILE);
  dort_runtime_t* runtime = NULL;
  dort_string_t discovery;
  dort_status_t* status = NULL;

  config.library_path_utf8 = partial_path;
  config.allowed_root_utf8 = allowed_root;
  status = dort_runtime_open(&config, &runtime);
  CHECK(status == NULL, "partial-discovery fake did not open");
  CHECK(runtime != NULL, "partial-discovery fake returned a null runtime");

  memset(&discovery, 0xff, sizeof(discovery));
  status = dort_runtime_available_providers_json(runtime, &discovery);
  CHECK(
      discovery.struct_size == (uint32_t)sizeof(discovery) &&
          discovery.data == NULL && discovery.length == 0u &&
          discovery.private_owner == NULL,
      "partial provider-discovery error did not clear its output");
  CHECK(
      expect_error(
          status,
          DORT_ERROR_DOMAIN_ORT_STATUS,
          DORT_ERROR_PROVIDER_UNSUPPORTED,
          "runtime_available_providers_json",
          "fake ONNX Runtime error") == 0,
      "partial provider-discovery error assertion failed");

  memset(&discovery, 0, sizeof(discovery));
  status = dort_runtime_available_providers_json(runtime, &discovery);
  CHECK(
      status == NULL,
      "provider discovery did not recover after releasing partial output");
  CHECK(
      discovery.data != NULL &&
          strstr((const char*)discovery.data, "CPUExecutionProvider") != NULL,
      "recovered provider discovery omitted its provider array");
  dort_string_release(&discovery);
  dort_runtime_release(runtime);
  return 0;
}

typedef struct concurrent_open_context {
  const char* good_path;
  const char* allowed_root;
} concurrent_open_context_t;

static void* concurrent_open_worker(void* opaque_context) {
  concurrent_open_context_t* context =
      (concurrent_open_context_t*)opaque_context;
  size_t iteration = 0u;
  for (iteration = 0u; iteration < 100u; ++iteration) {
    dort_runtime_config_t config = base_config(DORT_RUNTIME_SOURCE_FILE);
    dort_runtime_t* runtime = NULL;
    dort_status_t* status = NULL;
    config.library_path_utf8 = context->good_path;
    config.allowed_root_utf8 = context->allowed_root;
    status = dort_runtime_open(&config, &runtime);
    if (status != NULL || runtime == NULL) {
      dort_status_release(status);
      return (void*)(uintptr_t)1u;
    }
    dort_runtime_release(runtime);
  }
  return NULL;
}

static int test_concurrent_registry(
    const char* good_path,
    const char* allowed_root) {
  enum { worker_count = 8 };
  concurrent_open_context_t context = {good_path, allowed_root};
  pthread_t workers[worker_count];
  size_t index = 0u;
  for (index = 0u; index < worker_count; ++index) {
    CHECK(
        pthread_create(
            &workers[index], NULL, concurrent_open_worker, &context) == 0,
        "could not create runtime-registry stress worker");
  }
  for (index = 0u; index < worker_count; ++index) {
    void* result = NULL;
    CHECK(
        pthread_join(workers[index], &result) == 0,
        "could not join runtime-registry stress worker");
    CHECK(result == NULL, "concurrent runtime open/release failed");
  }
  return 0;
}

static int test_loader_failures(
    const char* good_path,
    const char* allowed_root,
    const char* outside_path,
    const char* missing_symbol_path,
    const char* unsupported_path) {
  dort_runtime_config_t config = base_config(DORT_RUNTIME_SOURCE_FILE);
  dort_runtime_t* runtime = NULL;
  dort_status_t* status = NULL;
  dort_status_t* retained_status = NULL;
  char retained_message[512];

  config.library_path_utf8 = outside_path;
  config.allowed_root_utf8 = allowed_root;
  status = dort_runtime_open(&config, &runtime);
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT, "runtime_open", "outside") == 0, "outside-root assertion failed");

  config = base_config(DORT_RUNTIME_SOURCE_FILE);
  config.library_path_utf8 = missing_symbol_path;
  status = dort_runtime_open(&config, &runtime);
  {
    char private_parent[4096];
    char* separator = NULL;
    CHECK(
        strlen(missing_symbol_path) < sizeof(private_parent),
        "missing-symbol test path is oversized");
    snprintf(private_parent, sizeof(private_parent), "%s", missing_symbol_path);
    separator = strrchr(private_parent, '/');
    CHECK(separator != NULL, "missing-symbol test path has no parent");
    *separator = '\0';
    CHECK(status != NULL, "missing-symbol runtime unexpectedly opened");
    CHECK(
        strstr(dort_status_message(status), private_parent) == NULL,
        "loader status exposed a private parent path");
  }
  CHECK(expect_error(status, DORT_ERROR_DOMAIN_LOADER, DORT_ERROR_SYMBOL_NOT_FOUND, "runtime_open", "OrtGetApiBase") == 0, "missing-symbol assertion failed");

  config = base_config(DORT_RUNTIME_SOURCE_FILE);
  config.library_path_utf8 = unsupported_path;
  retained_status = dort_runtime_open(&config, &runtime);
  CHECK(retained_status != NULL, "unsupported fake runtime unexpectedly opened");
  CHECK(dort_status_domain(retained_status) == DORT_ERROR_DOMAIN_ORT_API, "unsupported runtime domain mismatch");
  CHECK(dort_status_code(retained_status) == DORT_ERROR_ORT_API_UNAVAILABLE, "unsupported runtime code mismatch");
  snprintf(retained_message, sizeof(retained_message), "%s", dort_status_message(retained_status));

  CHECK(test_file_runtime(good_path, allowed_root) == 0, "good open during status-lifetime test failed");
  CHECK(strcmp(retained_message, dort_status_message(retained_status)) == 0, "status did not survive unrelated runtime lifetime");
  dort_status_release(retained_status);
  return 0;
}

int main(int argc, char** argv) {
  CHECK(
      argc == 9,
      "expected paths for good/root/missing/unsupported/discovery fakes");
  CHECK(test_build_contract() == 0, "build contract tests failed");
  CHECK(test_closed_config_validation(argv[1]) == 0, "configuration tests failed");
  CHECK(test_process_runtime(argv[1]) == 0, "process runtime tests failed");
  CHECK(test_file_runtime(argv[1], argv[2]) == 0, "file runtime tests failed");
  CHECK(test_concurrent_registry(argv[1], argv[2]) == 0, "runtime registry stress failed");
  CHECK(test_loader_failures(argv[1], argv[2], argv[4], argv[3], argv[4]) == 0, "loader failure tests failed");
  CHECK(
      test_provider_discovery_failure(argv[5], argv[6]) == 0,
      "provider discovery guard tests failed");
  CHECK(
      test_provider_partial_owner_cleanup(argv[7], argv[8]) == 0,
      "provider partial-owner cleanup tests failed");
  printf("Fonix native ABI/runtime tests passed.\n");
  return 0;
}
