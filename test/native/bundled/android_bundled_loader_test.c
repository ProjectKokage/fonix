#if !defined(__APPLE__)
#define _GNU_SOURCE 1
#endif

#include "dort_internal.h"

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if defined(FONIX_TEST_ANDROID_ABI_ARM64)
#define FONIX_TEST_ANDROID_ABI "arm64-v8a"
#define FONIX_TEST_ANDROID_OTHER_ABI "x86_64"
#elif defined(FONIX_TEST_ANDROID_ABI_X86_64)
#define FONIX_TEST_ANDROID_ABI "x86_64"
#define FONIX_TEST_ANDROID_OTHER_ABI "arm64-v8a"
#else
#error "The Android bundled loader test requires one closed test ABI"
#endif

#define FONIX_TEST_SHIM_SUFFIX \
  "!/lib/" FONIX_TEST_ANDROID_ABI "/libfonix_shim.so"

#define CHECK(condition, message)                                         \
  do {                                                                    \
    if (!(condition)) {                                                   \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message)); \
      return 1;                                                           \
    }                                                                     \
  } while (0)

static const char* g_shim_path = NULL;
static const char* g_runtime_path = NULL;
static void* g_runtime_symbol = NULL;
static int g_runtime_handle_cookie = 0;
static int g_runtime_missing = 0;
static int g_symbol_missing = 0;
static int g_loader_error_pending = 0;
static int g_dlopen_calls = 0;
static int g_dlsym_calls = 0;
static int g_dlclose_calls = 0;
static char g_opened_name[128];

static const OrtApiBase* ORT_API_CALL fake_ort_get_api_base(void) {
  return NULL;
}

static void reset_loader(
    const char* shim_path,
    int runtime_missing,
    int symbol_missing) {
  dort_ort_get_api_base_fn function = fake_ort_get_api_base;

  _Static_assert(
      sizeof(function) == sizeof(g_runtime_symbol),
      "POSIX data and function pointers must have matching sizes");
  memcpy(&g_runtime_symbol, &function, sizeof(g_runtime_symbol));
  g_shim_path = shim_path;
  g_runtime_path =
      "/data/app/~~install/dev.fonix.reference-Token/base.apk!"
      "/lib/" FONIX_TEST_ANDROID_ABI "/libonnxruntime.so";
  g_runtime_missing = runtime_missing;
  g_symbol_missing = symbol_missing;
  g_loader_error_pending = 0;
  g_dlopen_calls = 0;
  g_dlsym_calls = 0;
  g_dlclose_calls = 0;
  memset(g_opened_name, 0, sizeof(g_opened_name));
}

int fonix_test_dladdr(const void* address, Dl_info* information) {
  memset(information, 0, sizeof(*information));
  information->dli_fname =
      address == g_runtime_symbol ? g_runtime_path : g_shim_path;
  return information->dli_fname == NULL ? 0 : 1;
}

void* fonix_test_dlopen(const char* file_name, int flags) {
  (void)flags;
  ++g_dlopen_calls;
  if (file_name != NULL) {
    (void)snprintf(g_opened_name, sizeof(g_opened_name), "%s", file_name);
  }
  if (g_runtime_missing || file_name == NULL ||
      strcmp(file_name, "libonnxruntime.so") != 0) {
    g_loader_error_pending = 1;
    return NULL;
  }
  return &g_runtime_handle_cookie;
}

void* fonix_test_dlsym(void* handle, const char* symbol_name) {
  ++g_dlsym_calls;
  if (g_symbol_missing || handle != &g_runtime_handle_cookie ||
      strcmp(symbol_name, "OrtGetApiBase") != 0) {
    g_loader_error_pending = 1;
    return NULL;
  }
  return g_runtime_symbol;
}

char* fonix_test_dlerror(void) {
  static char redacted_loader_error[] = "test loader path must stay private";

  if (!g_loader_error_pending) {
    return NULL;
  }
  g_loader_error_pending = 0;
  return redacted_loader_error;
}

int fonix_test_dlclose(void* handle) {
  if (handle != &g_runtime_handle_cookie) {
    return -1;
  }
  ++g_dlclose_calls;
  return 0;
}

static int expect_rejected_shim_path(const char* shim_path) {
  dort_runtime_config_t config;
  dort_loaded_library_t library;
  dort_status_t* status = NULL;

  memset(&config, 0, sizeof(config));
  memset(&library, 0, sizeof(library));
  config.source_kind = DORT_RUNTIME_SOURCE_BUNDLED;
  reset_loader(shim_path, 0, 0);
  status = dort_loader_open(&config, &library);
  CHECK(status != NULL, "malformed Android shim path was accepted");
  CHECK(
      dort_status_domain(status) == DORT_ERROR_DOMAIN_LOADER,
      "malformed Android shim path returned the wrong domain");
  CHECK(
      dort_status_code(status) == DORT_ERROR_PLATFORM,
      "malformed Android shim path returned the wrong code");
  CHECK(g_dlopen_calls == 0, "rejected shim path reached the dynamic loader");
  CHECK(library.handle == NULL, "rejected shim path retained a handle");
  dort_status_release(status);
  return 0;
}

static int test_accepted_apk_paths(void) {
  static const char* const accepted_paths[] = {
      "/data/app/~~install/dev.fonix.reference-Token/base.apk"
      FONIX_TEST_SHIM_SUFFIX,
      "/data/app/~~install/dev.fonix.reference-Token/"
      "split_config.test.apk" FONIX_TEST_SHIM_SUFFIX,
  };
  size_t index = 0u;

  for (index = 0u; index < sizeof(accepted_paths) / sizeof(accepted_paths[0]);
       ++index) {
    dort_runtime_config_t config;
    dort_loaded_library_t library;
    dort_status_t* status = NULL;

    memset(&config, 0, sizeof(config));
    memset(&library, 0, sizeof(library));
    config.source_kind = DORT_RUNTIME_SOURCE_BUNDLED;
    reset_loader(accepted_paths[index], 0, 0);
    status = dort_loader_open(&config, &library);
    CHECK(status == NULL, "valid Android APK shim path was rejected");
    CHECK(g_dlopen_calls == 1, "Android bundled load was not attempted once");
    CHECK(
        strcmp(g_opened_name, "libonnxruntime.so") == 0,
        "Android bundled load used a path or alternate name");
    CHECK(g_dlsym_calls == 1, "OrtGetApiBase was not resolved once");
    CHECK(
        library.handle == &g_runtime_handle_cookie,
        "successful Android load did not retain its handle");
    CHECK(
        library.symbol_address == g_runtime_symbol,
        "successful Android load assigned the wrong ORT symbol");
    CHECK(
        library.get_api_base == fake_ort_get_api_base,
        "successful Android load assigned the wrong ORT function");
    CHECK(library.close_on_discard == 1, "Android loader handle is not owned");
    CHECK(
        library.canonical_identity_utf8 != NULL &&
            strcmp(library.canonical_identity_utf8, g_runtime_path) == 0,
        "Android runtime identity did not come from the loaded symbol");
    dort_loader_discard(&library);
    CHECK(g_dlclose_calls == 1, "Android loader handle was not closed once");
    CHECK(library.handle == NULL, "discard did not clear the loader state");
  }
  return 0;
}

static int test_rejected_apk_paths(void) {
  static const char* const rejected_paths[] = {
      "/tmp/base.apk" FONIX_TEST_SHIM_SUFFIX,
      "/vendor/app/base.apk" FONIX_TEST_SHIM_SUFFIX,
      "/data/app/base.apk!/lib/" FONIX_TEST_ANDROID_OTHER_ABI
      "/libfonix_shim.so",
      "/data/app/base.apk!/lib/" FONIX_TEST_ANDROID_ABI "/libforeign.so",
      "/data/app/base.zip" FONIX_TEST_SHIM_SUFFIX,
      "/data/app/dev.fonix/../base.apk" FONIX_TEST_SHIM_SUFFIX,
      "/data/app/dev.fonix//base.apk" FONIX_TEST_SHIM_SUFFIX,
      "/data/app/dev.fonix/base.apk!" FONIX_TEST_SHIM_SUFFIX,
      "/data/app/dev.fonix/base.apk/lib/" FONIX_TEST_ANDROID_ABI
      "/libfonix_shim.so",
      "/data/app/dev fonix/base.apk" FONIX_TEST_SHIM_SUFFIX,
      "/data/app/dev\tfonix/base.apk" FONIX_TEST_SHIM_SUFFIX,
  };
  size_t index = 0u;

  for (index = 0u; index < sizeof(rejected_paths) / sizeof(rejected_paths[0]);
       ++index) {
    CHECK(
        expect_rejected_shim_path(rejected_paths[index]) == 0,
        "Android shim path rejection failed");
  }
  return 0;
}

static int test_missing_runtime(void) {
  static const char shim_path[] =
      "/data/app/~~install/dev.fonix.reference-Token/base.apk"
      FONIX_TEST_SHIM_SUFFIX;
  dort_runtime_config_t config;
  dort_loaded_library_t library;
  dort_status_t* status = NULL;

  memset(&config, 0, sizeof(config));
  memset(&library, 0, sizeof(library));
  config.source_kind = DORT_RUNTIME_SOURCE_BUNDLED;
  reset_loader(shim_path, 1, 0);
  status = dort_loader_open(&config, &library);
  CHECK(status != NULL, "missing Android ONNX Runtime was accepted");
  CHECK(
      dort_status_domain(status) == DORT_ERROR_DOMAIN_LOADER,
      "missing Android runtime returned the wrong domain");
  CHECK(
      dort_status_code(status) == DORT_ERROR_RUNTIME_NOT_FOUND,
      "missing Android runtime returned the wrong code");
  CHECK(g_dlopen_calls == 1, "missing Android runtime was searched repeatedly");
  CHECK(
      strcmp(g_opened_name, "libonnxruntime.so") == 0,
      "missing Android runtime used a path or alternate name");
  CHECK(g_dlsym_calls == 0, "missing Android runtime reached symbol lookup");
  CHECK(g_dlclose_calls == 0, "missing Android runtime closed a fake handle");
  CHECK(library.handle == NULL, "missing Android runtime retained a handle");
  CHECK(
      strstr(dort_status_message(status), shim_path) == NULL &&
          strstr(dort_status_message(status), "test loader path") == NULL,
      "missing Android runtime exposed loader details");
  dort_status_release(status);
  return 0;
}

static int test_missing_symbol(void) {
  static const char shim_path[] =
      "/data/app/~~install/dev.fonix.reference-Token/base.apk"
      FONIX_TEST_SHIM_SUFFIX;
  dort_runtime_config_t config;
  dort_loaded_library_t library;
  dort_status_t* status = NULL;

  memset(&config, 0, sizeof(config));
  memset(&library, 0, sizeof(library));
  config.source_kind = DORT_RUNTIME_SOURCE_BUNDLED;
  reset_loader(shim_path, 0, 1);
  status = dort_loader_open(&config, &library);
  CHECK(status != NULL, "Android runtime without OrtGetApiBase was accepted");
  CHECK(
      dort_status_domain(status) == DORT_ERROR_DOMAIN_LOADER,
      "missing Android symbol returned the wrong domain");
  CHECK(
      dort_status_code(status) == DORT_ERROR_SYMBOL_NOT_FOUND,
      "missing Android symbol returned the wrong code");
  CHECK(g_dlopen_calls == 1, "missing Android symbol reopened the runtime");
  CHECK(g_dlsym_calls == 1, "missing Android symbol was searched repeatedly");
  CHECK(g_dlclose_calls == 1, "missing Android symbol did not close its handle");
  CHECK(library.handle == NULL, "missing Android symbol retained a handle");
  dort_status_release(status);
  return 0;
}

int main(void) {
  CHECK(test_accepted_apk_paths() == 0, "accepted APK path tests failed");
  CHECK(test_rejected_apk_paths() == 0, "rejected APK path tests failed");
  CHECK(test_missing_runtime() == 0, "missing runtime test failed");
  CHECK(test_missing_symbol() == 0, "missing symbol test failed");
  puts("Fonix Android bundled loader tests passed.");
  return 0;
}
