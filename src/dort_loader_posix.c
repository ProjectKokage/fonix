#if defined(__APPLE__)
#define _DARWIN_C_SOURCE 1
#else
#define _GNU_SOURCE 1
#endif
#define _POSIX_C_SOURCE 200809L

#include "dort_internal.h"

#if !defined(_WIN32)

#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static int dort_path_is_within(const char* path, const char* root) {
  const size_t root_length = strlen(root);
  if (strcmp(root, "/") == 0) {
    return path[0] == '/';
  }
  return strncmp(path, root, root_length) == 0 &&
         (path[root_length] == '\0' || path[root_length] == '/');
}

static dort_status_t* dort_resolve_file_path(
    const char* path,
    const char* allowed_root,
    char** out_canonical_path) {
  char* canonical_path = NULL;
  char* canonical_root = NULL;
  struct stat path_status;
  struct stat root_status;
  int error_number = 0;

  *out_canonical_path = NULL;
  if (path == NULL || path[0] != '/') {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_PATH_NOT_ABSOLUTE,
        0,
        "runtime_open",
        "The runtime library path must be absolute.");
  }
  canonical_path = dort_memory_realpath(path);
  if (canonical_path == NULL) {
    error_number = errno;
    if (error_number == ENOMEM) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "runtime_open",
          "Could not allocate the canonical runtime library path.");
    }
    return dort_status_createf(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_RUNTIME_NOT_FOUND,
        0,
        "runtime_open",
        "Could not canonicalize the runtime library path (platform error %d).",
        error_number);
  }
  if (stat(canonical_path, &path_status) != 0 || !S_ISREG(path_status.st_mode)) {
    error_number = errno;
    free(canonical_path);
    return dort_status_createf(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_RUNTIME_NOT_FOUND,
        0,
        "runtime_open",
        "The canonical runtime library path is not a regular file (platform error %d).",
        error_number);
  }

  if (allowed_root != NULL) {
    if (allowed_root[0] != '/') {
      free(canonical_path);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PATH_NOT_ABSOLUTE,
          0,
          "runtime_open",
          "The allowed runtime root must be absolute.");
    }
    canonical_root = dort_memory_realpath(allowed_root);
    if (canonical_root == NULL) {
      error_number = errno;
      free(canonical_path);
      if (error_number == ENOMEM) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_ALLOCATION,
            DORT_ERROR_ALLOCATION_FAILED,
            0,
            "runtime_open",
            "Could not allocate the canonical allowed runtime root.");
      }
      return dort_status_createf(
          DORT_ERROR_DOMAIN_LOADER,
          DORT_ERROR_RUNTIME_NOT_FOUND,
          0,
          "runtime_open",
          "Could not canonicalize the allowed runtime root (platform error %d).",
          error_number);
    }
    if (stat(canonical_root, &root_status) != 0 ||
        !S_ISDIR(root_status.st_mode)) {
      error_number = errno;
      free(canonical_root);
      free(canonical_path);
      return dort_status_createf(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "runtime_open",
          "The allowed runtime root is not a directory (platform error %d).",
          error_number);
    }
    if (!dort_path_is_within(canonical_path, canonical_root)) {
      free(canonical_root);
      free(canonical_path);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT,
          0,
          "runtime_open",
          "The canonical runtime library path is outside the allowed root.");
    }
    free(canonical_root);
  }

  *out_canonical_path = canonical_path;
  return NULL;
}

static int dort_is_allowed_process_library_name(const char* name) {
#if defined(__APPLE__)
  return strcmp(name, "libonnxruntime.dylib") == 0;
#else
  return strcmp(name, "libonnxruntime.so") == 0;
#endif
}

static dort_status_t* dort_assign_symbol(
    void* handle,
    void* symbol,
    const char* fallback_identity,
    int close_on_discard,
    dort_loaded_library_t* out_library) {
  Dl_info symbol_info;
  char* canonical_symbol_path = NULL;
  const char* identity = fallback_identity;
  size_t identity_length = 0u;
  int validation = DORT_ERROR_NONE;

  if (symbol == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_SYMBOL_NOT_FOUND,
        0,
        "runtime_open",
        "The selected native library does not export OrtGetApiBase.");
  }
  _Static_assert(
      sizeof(out_library->get_api_base) == sizeof(symbol),
      "POSIX data and function pointers must have matching sizes");
  memcpy(&out_library->get_api_base, &symbol, sizeof(symbol));

  memset(&symbol_info, 0, sizeof(symbol_info));
  if (dladdr(symbol, &symbol_info) != 0 && symbol_info.dli_fname != NULL) {
    canonical_symbol_path = dort_memory_realpath(symbol_info.dli_fname);
    if (canonical_symbol_path == NULL && errno == ENOMEM) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "runtime_open",
          "Could not allocate the canonical loaded runtime identity.");
    }
    identity = canonical_symbol_path == NULL ? symbol_info.dli_fname
                                             : canonical_symbol_path;
  }
  validation = dort_bounded_utf8_length(
      identity, DORT_MAX_PATH_BYTES, 0, &identity_length);
  if (validation != DORT_ERROR_NONE) {
    free(canonical_symbol_path);
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        validation,
        0,
        "runtime_open",
        "The resolved runtime identity is invalid or too long.");
  }
  out_library->canonical_identity_utf8 =
      dort_copy_c_string(identity, identity_length);
  free(canonical_symbol_path);
  if (out_library->canonical_identity_utf8 == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_open",
        "Could not copy the runtime library identity.");
  }
  out_library->handle = handle;
  out_library->symbol_address = symbol;
  out_library->close_on_discard = close_on_discard;
  return NULL;
}

static dort_status_t* dort_open_file(
    const dort_runtime_config_t* config,
    dort_loaded_library_t* out_library) {
  char* canonical_path = NULL;
  void* handle = NULL;
  void* symbol = NULL;
  const char* loader_error = NULL;
  dort_status_t* status = dort_resolve_file_path(
      config->library_path_utf8, config->allowed_root_utf8, &canonical_path);
  if (status != NULL) {
    return status;
  }

  dlerror();
  handle = dlopen(canonical_path, RTLD_NOW | RTLD_LOCAL);
  if (handle == NULL) {
    (void)dlerror();
    status = dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_RUNTIME_NOT_FOUND,
        0,
        "runtime_open",
        "Could not load the selected runtime library; loader details were redacted.");
    free(canonical_path);
    return status;
  }
  dlerror();
  symbol = dlsym(handle, "OrtGetApiBase");
  loader_error = dlerror();
  if (loader_error != NULL || symbol == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_SYMBOL_NOT_FOUND,
        0,
        "runtime_open",
        "The selected native library does not export OrtGetApiBase.");
    dlclose(handle);
    free(canonical_path);
    return status;
  }
  status = dort_assign_symbol(
      handle, symbol, canonical_path, 1, out_library);
  if (status != NULL) {
    dlclose(handle);
  }
  free(canonical_path);
  return status;
}

#if defined(__ANDROID__) || defined(FONIX_TEST_ANDROID_BUNDLED_LOADER)
static const char* dort_android_bundled_shim_suffix(void) {
#if defined(FONIX_TEST_ANDROID_ABI_ARM64)
  return "!/lib/arm64-v8a/libfonix_shim.so";
#elif defined(FONIX_TEST_ANDROID_ABI_X86_64)
  return "!/lib/x86_64/libfonix_shim.so";
#elif defined(__aarch64__)
  return "!/lib/arm64-v8a/libfonix_shim.so";
#elif defined(__x86_64__)
  return "!/lib/x86_64/libfonix_shim.so";
#else
  return NULL;
#endif
}

static int dort_android_archive_path_is_valid(
    const char* path,
    size_t path_length) {
  static const char install_root[] = "/data/app/";
  static const char archive_suffix[] = ".apk";
  size_t segment_start = 1u;
  size_t index = 0u;

  if (path_length <= sizeof(install_root) - 1u ||
      strncmp(path, install_root, sizeof(install_root) - 1u) != 0 ||
      path_length < sizeof(archive_suffix) - 1u ||
      memcmp(
          path + path_length - (sizeof(archive_suffix) - 1u),
          archive_suffix,
          sizeof(archive_suffix) - 1u) != 0) {
    return 0;
  }
  for (index = 0u; index < path_length; ++index) {
    const unsigned char character = (unsigned char)path[index];
    if (character <= 0x20u || character >= 0x7fu || character == '\\' ||
        character == '!') {
      return 0;
    }
  }
  for (index = 1u; index <= path_length; ++index) {
    if (index == path_length || path[index] == '/') {
      const size_t segment_length = index - segment_start;
      if (segment_length == 0u ||
          (segment_length == 1u && path[segment_start] == '.') ||
          (segment_length == 2u && path[segment_start] == '.' &&
           path[segment_start + 1u] == '.')) {
        return 0;
      }
      segment_start = index + 1u;
    }
  }
  return 1;
}

static int dort_android_bundled_shim_path_is_valid(const char* shim_path) {
  const char* shim_suffix = dort_android_bundled_shim_suffix();
  size_t path_length = 0u;
  size_t suffix_length = 0u;
  int validation = DORT_ERROR_NONE;

  if (shim_suffix == NULL) {
    return 0;
  }
  validation = dort_bounded_utf8_length(
      shim_path, DORT_MAX_PATH_BYTES, 0, &path_length);
  if (validation != DORT_ERROR_NONE) {
    return 0;
  }
  suffix_length = strlen(shim_suffix);
  if (path_length <= suffix_length ||
      memcmp(
          shim_path + path_length - suffix_length,
          shim_suffix,
          suffix_length) != 0) {
    return 0;
  }
  return dort_android_archive_path_is_valid(
      shim_path, path_length - suffix_length);
}

static dort_status_t* dort_open_android_bundled(
    const char* shim_path,
    dort_loaded_library_t* out_library) {
  static const char runtime_name[] = "libonnxruntime.so";
  void* handle = NULL;
  void* symbol = NULL;
  const char* loader_error = NULL;
  dort_status_t* status = NULL;

  if (!dort_android_bundled_shim_path_is_valid(shim_path)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_PLATFORM,
        0,
        "runtime_open",
        "The bundled Fonix shim is not in the exact Android APK ABI layout.");
  }

  /*
   * Bionic resolves this exact basename in the calling shim's application
   * linker namespace. Do not add a file path, search list, or cwd fallback.
   */
  dlerror();
  handle = dlopen(runtime_name, RTLD_NOW | RTLD_LOCAL);
  if (handle == NULL) {
    (void)dlerror();
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_RUNTIME_NOT_FOUND,
        0,
        "runtime_open",
        "Could not load the bundled ONNX Runtime from the Android application "
        "linker namespace; loader details were redacted.");
  }
  dlerror();
  symbol = dlsym(handle, "OrtGetApiBase");
  loader_error = dlerror();
  if (loader_error != NULL || symbol == NULL) {
    dlclose(handle);
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_SYMBOL_NOT_FOUND,
        0,
        "runtime_open",
        "The bundled ONNX Runtime does not export OrtGetApiBase.");
  }
  status = dort_assign_symbol(handle, symbol, runtime_name, 1, out_library);
  if (status != NULL) {
    dlclose(handle);
  }
  return status;
}
#else
static const char* dort_bundled_runtime_name(void) {
#if defined(__APPLE__)
  return "libonnxruntime.1.dylib";
#else
  return "libonnxruntime.so.1";
#endif
}

static dort_status_t* dort_allocate_adjacent_path(
    const char* directory,
    const char* file_name,
    char** out_path) {
  size_t path_length = 0u;

  *out_path = NULL;
  if (!dort_checked_add_size(
          strlen(directory), strlen(file_name), &path_length) ||
      !dort_checked_add_size(path_length, 2u, &path_length) ||
      path_length > DORT_MAX_PATH_BYTES) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "runtime_open",
        "The adjacent bundled runtime path exceeds the ABI limit.");
  }
  *out_path = (char*)dort_memory_allocate(path_length);
  if (*out_path == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_open",
        "Could not allocate the adjacent bundled runtime path.");
  }
  (void)snprintf(
      *out_path,
      path_length,
      "%s%s%s",
      directory,
      strcmp(directory, "/") == 0 ? "" : "/",
      file_name);
  return NULL;
}

#if defined(__APPLE__)
static dort_status_t* dort_open_macos_flutter_framework_runtime(
    const dort_runtime_config_t* config,
    const char* shim_path,
    dort_loaded_library_t* out_library) {
  static const char shim_suffix[] =
      "/fonix_shim.framework/Versions/A/fonix_shim";
  static const char runtime_suffix[] =
      "/onnxruntime.1.framework/Versions/A/onnxruntime.1";
  const size_t shim_path_length = strlen(shim_path);
  const size_t shim_suffix_length = sizeof(shim_suffix) - 1u;
  const size_t runtime_suffix_length = sizeof(runtime_suffix) - 1u;
  size_t framework_root_length = 0u;
  size_t runtime_path_length = 0u;
  char* framework_root = NULL;
  char* runtime_path = NULL;
  dort_runtime_config_t framework_config = *config;
  dort_status_t* status = NULL;

  if (shim_path_length <= shim_suffix_length ||
      strcmp(shim_path + shim_path_length - shim_suffix_length, shim_suffix) !=
          0) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_PLATFORM,
        0,
        "runtime_open",
        "The bundled Fonix shim is neither adjacent to ONNX Runtime nor in "
        "the exact Flutter macOS framework layout.");
  }
  framework_root_length = shim_path_length - shim_suffix_length;
  if (!dort_checked_add_size(
          framework_root_length,
          runtime_suffix_length,
          &runtime_path_length) ||
      !dort_checked_add_size(runtime_path_length, 1u, &runtime_path_length) ||
      runtime_path_length > DORT_MAX_PATH_BYTES) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "runtime_open",
        "The Flutter framework runtime path exceeds the ABI limit.");
  }
  framework_root = (char*)dort_memory_allocate(framework_root_length + 1u);
  runtime_path = (char*)dort_memory_allocate(runtime_path_length);
  if (framework_root == NULL || runtime_path == NULL) {
    free(runtime_path);
    free(framework_root);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_open",
        "Could not allocate the Flutter framework runtime path.");
  }
  memcpy(framework_root, shim_path, framework_root_length);
  framework_root[framework_root_length] = '\0';
  (void)snprintf(
      runtime_path,
      runtime_path_length,
      "%s%s",
      framework_root,
      runtime_suffix);
  framework_config.library_path_utf8 = runtime_path;
  framework_config.allowed_root_utf8 = framework_root;
  status = dort_open_file(&framework_config, out_library);
  free(runtime_path);
  free(framework_root);
  return status;
}
#endif
#endif

static dort_status_t* dort_open_bundled(
    const dort_runtime_config_t* config,
    dort_loaded_library_t* out_library) {
  dort_status_t* (*loader_function)(
      const dort_runtime_config_t*, dort_loaded_library_t*) = dort_loader_open;
  void* loader_address = NULL;
  Dl_info shim_info;
#if !defined(__ANDROID__) && !defined(FONIX_TEST_ANDROID_BUNDLED_LOADER)
  char* shim_path = NULL;
  char* shim_directory = NULL;
  char* separator = NULL;
  char* runtime_path = NULL;
  const char* runtime_name = dort_bundled_runtime_name();
  size_t shim_directory_length = 0u;
  struct stat adjacent_status;
  dort_runtime_config_t adjacent_config = *config;
  dort_status_t* status = NULL;
#else
  (void)config;
#endif

  _Static_assert(
      sizeof(loader_function) == sizeof(loader_address),
      "POSIX data and function pointers must have matching sizes");
  memcpy(&loader_address, &loader_function, sizeof(loader_address));
  memset(&shim_info, 0, sizeof(shim_info));
  if (dladdr(loader_address, &shim_info) == 0 || shim_info.dli_fname == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_PLATFORM,
        0,
        "runtime_open",
        "Could not identify the bundled Fonix shim location.");
  }
#if defined(__ANDROID__) || defined(FONIX_TEST_ANDROID_BUNDLED_LOADER)
  return dort_open_android_bundled(shim_info.dli_fname, out_library);
#else
  shim_path = dort_memory_realpath(shim_info.dli_fname);
  if (shim_path == NULL) {
    if (errno == ENOMEM) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "runtime_open",
          "Could not allocate the canonical bundled Fonix shim path.");
    }
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_RUNTIME_NOT_FOUND,
        0,
        "runtime_open",
        "Could not canonicalize the bundled Fonix shim location.");
  }
  separator = strrchr(shim_path, '/');
  if (separator == NULL) {
    free(shim_path);
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_PLATFORM,
        0,
        "runtime_open",
        "The bundled Fonix shim location has no absolute parent directory.");
  }
  shim_directory_length = separator == shim_path
                              ? 1u
                              : (size_t)(separator - shim_path);
  shim_directory = (char*)dort_memory_allocate(shim_directory_length + 1u);
  if (shim_directory == NULL) {
    free(shim_path);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_open",
        "Could not allocate the bundled Fonix shim directory.");
  }
  memcpy(shim_directory, shim_path, shim_directory_length);
  shim_directory[shim_directory_length] = '\0';
  status = dort_allocate_adjacent_path(
      shim_directory, runtime_name, &runtime_path);
  if (status != NULL) {
    free(shim_directory);
    free(shim_path);
    return status;
  }
  adjacent_config.library_path_utf8 = runtime_path;
  adjacent_config.allowed_root_utf8 = shim_directory;
  if (lstat(runtime_path, &adjacent_status) == 0 || errno != ENOENT) {
    status = dort_open_file(&adjacent_config, out_library);
  } else {
#if defined(__APPLE__)
    status = dort_open_macos_flutter_framework_runtime(
        config, shim_path, out_library);
#else
    status = dort_open_file(&adjacent_config, out_library);
#endif
  }
  free(runtime_path);
  free(shim_directory);
  free(shim_path);
  return status;
#endif
}

static dort_status_t* dort_open_process(
    const dort_runtime_config_t* config,
    dort_loaded_library_t* out_library) {
#if defined(__APPLE__)
  static const char default_name[] = "libonnxruntime.dylib";
#else
  static const char default_name[] = "libonnxruntime.so";
#endif
  void* symbol = NULL;
  const char* loader_error = NULL;
  size_t count = config->preferred_library_name_count;
  size_t index = 0u;

  dlerror();
  symbol = dlsym(RTLD_DEFAULT, "OrtGetApiBase");
  loader_error = dlerror();
  if (loader_error == NULL && symbol != NULL) {
    Dl_info symbol_info;
    void* retained_handle = NULL;
    memset(&symbol_info, 0, sizeof(symbol_info));
    if (dladdr(symbol, &symbol_info) == 0 || symbol_info.dli_fname == NULL) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_LOADER,
          DORT_ERROR_PLATFORM,
          0,
          "runtime_open",
          "Could not identify the process-owned ONNX Runtime library.");
    }
    dlerror();
    retained_handle = dlopen(symbol_info.dli_fname, RTLD_NOW | RTLD_LOCAL);
    if (retained_handle == NULL) {
      (void)dlerror();
      return dort_status_create(
          DORT_ERROR_DOMAIN_LOADER,
          DORT_ERROR_RUNTIME_NOT_FOUND,
          0,
          "runtime_open",
          "Could not retain the process-owned ONNX Runtime library; loader details were redacted.");
    }
    {
      dort_status_t* status = dort_assign_symbol(
          retained_handle, symbol, symbol_info.dli_fname, 1, out_library);
      if (status != NULL) {
        dlclose(retained_handle);
      }
      return status;
    }
  }

  if (count == 0u) {
    count = 1u;
  }
  for (index = 0u; index < count; ++index) {
    const char* name = config->preferred_library_name_count == 0u
                           ? default_name
                           : config->preferred_library_names_utf8[index];
    void* handle = NULL;
    if (!dort_is_allowed_process_library_name(name)) {
      return dort_status_createf(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "runtime_open",
          "The process runtime library name is not allowlisted: %s",
          name);
    }
    dlerror();
    handle = dlopen(name, RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
      loader_error = dlerror();
      continue;
    }
    dlerror();
    symbol = dlsym(handle, "OrtGetApiBase");
    loader_error = dlerror();
    if (loader_error == NULL && symbol != NULL) {
      dort_status_t* status =
          dort_assign_symbol(handle, symbol, name, 1, out_library);
      if (status != NULL) {
        dlclose(handle);
      }
      return status;
    }
    dlclose(handle);
  }

  return dort_status_create(
      DORT_ERROR_DOMAIN_LOADER,
      DORT_ERROR_RUNTIME_NOT_FOUND,
      0,
      "runtime_open",
      "No process-owned ONNX Runtime exposing OrtGetApiBase could be resolved.");
}

dort_status_t* dort_loader_open(
    const dort_runtime_config_t* config,
    dort_loaded_library_t* out_library) {
  memset(out_library, 0, sizeof(*out_library));

#if defined(FONIX_RUNTIME_PROFILE_LINKED)
  if (config->source_kind == DORT_RUNTIME_SOURCE_LINKED) {
    void* symbol = NULL;
    dort_ort_get_api_base_fn function = OrtGetApiBase;
    _Static_assert(
        sizeof(function) == sizeof(symbol),
        "POSIX data and function pointers must have matching sizes");
    memcpy(&symbol, &function, sizeof(symbol));
    return dort_assign_symbol(NULL, symbol, "linked:OrtGetApiBase", 0, out_library);
  }
#endif
  if (config->source_kind == DORT_RUNTIME_SOURCE_PROCESS) {
    return dort_open_process(config, out_library);
  }
  if (config->source_kind == DORT_RUNTIME_SOURCE_FILE) {
    return dort_open_file(config, out_library);
  }
  if (config->source_kind == DORT_RUNTIME_SOURCE_BUNDLED) {
    return dort_open_bundled(config, out_library);
  }
  return dort_status_create(
      DORT_ERROR_DOMAIN_UNSUPPORTED,
      DORT_ERROR_SOURCE_UNSUPPORTED,
      0,
      "runtime_open",
      "The runtime source is unsupported by this loader.");
}

void dort_loader_discard(dort_loaded_library_t* library) {
  if (library == NULL) {
    return;
  }
  if (library->close_on_discard && library->handle != NULL) {
    dlclose(library->handle);
  }
  free(library->canonical_identity_utf8);
  memset(library, 0, sizeof(*library));
}

void* dort_loader_find_optional_symbol(
    const dort_loaded_library_t* library,
    const char* symbol_name) {
  void* symbol = NULL;
  const char* error = NULL;
  if (library == NULL || symbol_name == NULL || symbol_name[0] == '\0') {
    return NULL;
  }
  dlerror();
  symbol = dlsym(
      library->handle == NULL ? RTLD_DEFAULT : library->handle, symbol_name);
  error = dlerror();
  return error == NULL ? symbol : NULL;
}

#endif /* !_WIN32 */
