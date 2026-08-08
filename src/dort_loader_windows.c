#include "dort_internal.h"

#if defined(_WIN32)

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static dort_status_t* dort_windows_error(
    int32_t code,
    const char* operation,
    const char* message,
    DWORD windows_error) {
  return dort_status_createf(
      DORT_ERROR_DOMAIN_LOADER,
      code,
      0,
      operation,
      "%s (Windows error %lu).",
      message,
      (unsigned long)windows_error);
}

static wchar_t* dort_utf8_to_wide(const char* value) {
  int required = 0;
  wchar_t* output = NULL;
  if (value == NULL) {
    return NULL;
  }
  required = MultiByteToWideChar(
      CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, NULL, 0);
  if (required <= 0) {
    return NULL;
  }
  output = (wchar_t*)dort_memory_allocate_zeroed((size_t)required, sizeof(wchar_t));
  if (output == NULL) {
    return NULL;
  }
  if (MultiByteToWideChar(
          CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, output, required) <= 0) {
    free(output);
    return NULL;
  }
  return output;
}

static char* dort_wide_to_utf8(const wchar_t* value) {
  int required = 0;
  char* output = NULL;
  if (value == NULL) {
    return NULL;
  }
  required = WideCharToMultiByte(
      CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, NULL, 0, NULL, NULL);
  if (required <= 0) {
    return NULL;
  }
  output = (char*)dort_memory_allocate((size_t)required);
  if (output == NULL) {
    return NULL;
  }
  if (WideCharToMultiByte(
          CP_UTF8,
          WC_ERR_INVALID_CHARS,
          value,
          -1,
          output,
          required,
          NULL,
          NULL) <= 0) {
    free(output);
    return NULL;
  }
  return output;
}

static int dort_windows_path_is_absolute(const wchar_t* path) {
  if (path == NULL) {
    return 0;
  }
  if (((path[0] >= L'A' && path[0] <= L'Z') ||
       (path[0] >= L'a' && path[0] <= L'z')) &&
      path[1] == L':' && (path[2] == L'\\' || path[2] == L'/')) {
    return 1;
  }
  return path[0] == L'\\' && path[1] == L'\\';
}

static wchar_t* dort_final_path(const wchar_t* path, int directory) {
  HANDLE handle = INVALID_HANDLE_VALUE;
  DWORD required = 0;
  wchar_t* output = NULL;
  DWORD flags = FILE_ATTRIBUTE_NORMAL;

  if (directory) {
    flags = FILE_FLAG_BACKUP_SEMANTICS;
  }
  handle = CreateFileW(
      path,
      FILE_READ_ATTRIBUTES,
      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
      NULL,
      OPEN_EXISTING,
      flags,
      NULL);
  if (handle == INVALID_HANDLE_VALUE) {
    return NULL;
  }
  required = GetFinalPathNameByHandleW(handle, NULL, 0, FILE_NAME_NORMALIZED);
  if (required == 0) {
    CloseHandle(handle);
    return NULL;
  }
  output = (wchar_t*)dort_memory_allocate_zeroed((size_t)required + 1u, sizeof(wchar_t));
  if (output == NULL) {
    CloseHandle(handle);
    return NULL;
  }
  if (GetFinalPathNameByHandleW(
          handle, output, required + 1u, FILE_NAME_NORMALIZED) == 0) {
    free(output);
    output = NULL;
  }
  CloseHandle(handle);
  return output;
}

static int dort_windows_path_is_within(
    const wchar_t* path,
    const wchar_t* root) {
  size_t root_length = wcslen(root);
  while (root_length > 0u &&
         (root[root_length - 1u] == L'\\' || root[root_length - 1u] == L'/')) {
    --root_length;
  }
  return _wcsnicmp(path, root, root_length) == 0 &&
         (path[root_length] == L'\0' || path[root_length] == L'\\' ||
          path[root_length] == L'/');
}

static dort_status_t* dort_assign_windows_module(
    HMODULE module,
    int close_on_discard,
    dort_loaded_library_t* out_library) {
  FARPROC procedure = GetProcAddress(module, "OrtGetApiBase");
  wchar_t module_path[32768];
  DWORD module_path_length = 0;
  char* identity = NULL;
  void* symbol = NULL;

  if (procedure == NULL) {
    return dort_windows_error(
        DORT_ERROR_SYMBOL_NOT_FOUND,
        "runtime_open",
        "The selected native library does not export OrtGetApiBase",
        GetLastError());
  }
  module_path_length = GetModuleFileNameW(
      module, module_path, (DWORD)(sizeof(module_path) / sizeof(module_path[0])));
  if (module_path_length == 0 ||
      module_path_length >= (DWORD)(sizeof(module_path) / sizeof(module_path[0]))) {
    return dort_windows_error(
        DORT_ERROR_PLATFORM,
        "runtime_open",
        "Could not determine the loaded runtime identity",
        GetLastError());
  }
  identity = dort_wide_to_utf8(module_path);
  if (identity == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_open",
        "Could not encode the loaded runtime identity as UTF-8.");
  }
  memcpy(&symbol, &procedure, sizeof(symbol));
  memcpy(&out_library->get_api_base, &procedure, sizeof(procedure));
  out_library->handle = module;
  out_library->symbol_address = symbol;
  out_library->canonical_identity_utf8 = identity;
  out_library->close_on_discard = close_on_discard;
  return NULL;
}

static dort_status_t* dort_open_windows_file(
    const dort_runtime_config_t* config,
    dort_loaded_library_t* out_library) {
  wchar_t* requested_path = dort_utf8_to_wide(config->library_path_utf8);
  wchar_t* canonical_path = NULL;
  wchar_t* requested_root = NULL;
  wchar_t* canonical_root = NULL;
  HMODULE module = NULL;
  DWORD error_code = ERROR_SUCCESS;
  dort_status_t* status = NULL;

  if (requested_path == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_UTF8,
        0,
        "runtime_open",
        "The runtime path is not valid UTF-8.");
  }
  if (!dort_windows_path_is_absolute(requested_path)) {
    free(requested_path);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_PATH_NOT_ABSOLUTE,
        0,
        "runtime_open",
        "The runtime library path must be absolute.");
  }
  canonical_path = dort_final_path(requested_path, 0);
  free(requested_path);
  if (canonical_path == NULL) {
    return dort_windows_error(
        DORT_ERROR_RUNTIME_NOT_FOUND,
        "runtime_open",
        "Could not resolve the runtime library path",
        GetLastError());
  }

  if (config->allowed_root_utf8 != NULL) {
    requested_root = dort_utf8_to_wide(config->allowed_root_utf8);
    if (requested_root == NULL || !dort_windows_path_is_absolute(requested_root)) {
      free(requested_root);
      free(canonical_path);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_PATH_NOT_ABSOLUTE,
          0,
          "runtime_open",
          "The allowed runtime root must be an absolute UTF-8 path.");
    }
    canonical_root = dort_final_path(requested_root, 1);
    free(requested_root);
    if (canonical_root == NULL) {
      error_code = GetLastError();
      free(canonical_path);
      return dort_windows_error(
          DORT_ERROR_RUNTIME_NOT_FOUND,
          "runtime_open",
          "Could not resolve the allowed runtime root",
          error_code);
    }
    if (!dort_windows_path_is_within(canonical_path, canonical_root)) {
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

  module = LoadLibraryExW(
      canonical_path,
      NULL,
      LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
  free(canonical_path);
  if (module == NULL) {
    return dort_windows_error(
        DORT_ERROR_RUNTIME_NOT_FOUND,
        "runtime_open",
        "Could not securely load the selected runtime library",
        GetLastError());
  }
  status = dort_assign_windows_module(module, 1, out_library);
  if (status != NULL) {
    FreeLibrary(module);
  }
  return status;
}

static dort_status_t* dort_open_windows_bundled(
    const dort_runtime_config_t* config,
    dort_loaded_library_t* out_library) {
  static const wchar_t runtime_name[] = L"onnxruntime.dll";
  dort_status_t* (*loader_function)(
      const dort_runtime_config_t*, dort_loaded_library_t*) = dort_loader_open;
  const wchar_t* loader_address = NULL;
  HMODULE shim_module = NULL;
  wchar_t shim_path[32768];
  DWORD shim_path_length = 0u;
  wchar_t* separator = NULL;
  size_t directory_length = 0u;
  size_t runtime_path_length = 0u;
  wchar_t* runtime_path = NULL;
  char* directory_utf8 = NULL;
  char* runtime_path_utf8 = NULL;
  size_t ignored = 0u;
  dort_runtime_config_t adjacent_config = *config;
  dort_status_t* status = NULL;

  _Static_assert(
      sizeof(loader_function) == sizeof(loader_address),
      "Windows data and function pointers must have matching sizes");
  memcpy(&loader_address, &loader_function, sizeof(loader_address));
  if (!GetModuleHandleExW(
          GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
              GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
          loader_address,
          &shim_module)) {
    return dort_windows_error(
        DORT_ERROR_PLATFORM,
        "runtime_open",
        "Could not identify the bundled Fonix shim module",
        GetLastError());
  }
  shim_path_length = GetModuleFileNameW(
      shim_module,
      shim_path,
      (DWORD)(sizeof(shim_path) / sizeof(shim_path[0])));
  if (shim_path_length == 0u ||
      shim_path_length >=
          (DWORD)(sizeof(shim_path) / sizeof(shim_path[0]))) {
    return dort_windows_error(
        DORT_ERROR_PLATFORM,
        "runtime_open",
        "Could not determine the bundled Fonix shim path",
        GetLastError());
  }
  separator = wcsrchr(shim_path, L'\\');
  if (separator == NULL) {
    separator = wcsrchr(shim_path, L'/');
  }
  if (separator == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_PLATFORM,
        0,
        "runtime_open",
        "The bundled Fonix shim path has no absolute parent directory.");
  }
  if (separator == shim_path + 2 && shim_path[1] == L':') {
    separator[1] = L'\0';
  } else {
    *separator = L'\0';
  }
  directory_length = wcslen(shim_path);
  if (directory_length > SIZE_MAX - (sizeof(runtime_name) / sizeof(wchar_t)) -
                             1u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "runtime_open",
        "The adjacent bundled runtime path exceeds the ABI limit.");
  }
  runtime_path_length = directory_length +
                        (sizeof(runtime_name) / sizeof(wchar_t)) + 1u;
  runtime_path =
      (wchar_t*)dort_memory_allocate_zeroed(runtime_path_length, sizeof(*runtime_path));
  if (runtime_path == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_open",
        "Could not allocate the adjacent bundled runtime path.");
  }
  (void)swprintf(
      runtime_path,
      runtime_path_length,
      L"%ls%ls%ls",
      shim_path,
      directory_length > 0u &&
              (shim_path[directory_length - 1u] == L'\\' ||
               shim_path[directory_length - 1u] == L'/')
          ? L""
          : L"\\",
      runtime_name);
  directory_utf8 = dort_wide_to_utf8(shim_path);
  runtime_path_utf8 = dort_wide_to_utf8(runtime_path);
  free(runtime_path);
  if (directory_utf8 == NULL || runtime_path_utf8 == NULL ||
      dort_bounded_utf8_length(
          directory_utf8, DORT_MAX_PATH_BYTES, 0, &ignored) !=
          DORT_ERROR_NONE ||
      dort_bounded_utf8_length(
          runtime_path_utf8, DORT_MAX_PATH_BYTES, 0, &ignored) !=
          DORT_ERROR_NONE) {
    free(directory_utf8);
    free(runtime_path_utf8);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "runtime_open",
        "The adjacent bundled runtime path cannot be represented within the ABI limit.");
  }
  adjacent_config.library_path_utf8 = runtime_path_utf8;
  adjacent_config.allowed_root_utf8 = directory_utf8;
  status = dort_open_windows_file(&adjacent_config, out_library);
  free(directory_utf8);
  free(runtime_path_utf8);
  return status;
}

static dort_status_t* dort_open_windows_process(
    const dort_runtime_config_t* config,
    dort_loaded_library_t* out_library) {
  static const char default_name[] = "onnxruntime.dll";
  size_t count = config->preferred_library_name_count;
  size_t index = 0u;
  DWORD last_error = ERROR_MOD_NOT_FOUND;

  if (count == 0u) {
    count = 1u;
  }
  for (index = 0u; index < count; ++index) {
    const char* name = config->preferred_library_name_count == 0u
                           ? default_name
                           : config->preferred_library_names_utf8[index];
    wchar_t* wide_name = NULL;
    HMODULE module = NULL;
    int close_on_discard = 0;
    dort_status_t* status = NULL;
    if (strcmp(name, default_name) != 0) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "runtime_open",
          "The process runtime library name is not allowlisted.");
    }
    wide_name = dort_utf8_to_wide(name);
    if (wide_name == NULL) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_UTF8,
          0,
          "runtime_open",
          "The process runtime library name is not valid UTF-8.");
    }
    if (!GetModuleHandleExW(0u, wide_name, &module)) {
      module = LoadLibraryExW(wide_name, NULL, LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    }
    close_on_discard = module != NULL;
    free(wide_name);
    if (module == NULL) {
      last_error = GetLastError();
      continue;
    }
    status = dort_assign_windows_module(module, close_on_discard, out_library);
    if (status == NULL) {
      return NULL;
    }
    if (close_on_discard) {
      FreeLibrary(module);
    }
    dort_status_release(status);
  }
  return dort_windows_error(
      DORT_ERROR_RUNTIME_NOT_FOUND,
      "runtime_open",
      "No process-owned ONNX Runtime could be resolved",
      last_error);
}

dort_status_t* dort_loader_open(
    const dort_runtime_config_t* config,
    dort_loaded_library_t* out_library) {
  memset(out_library, 0, sizeof(*out_library));
#if defined(FONIX_RUNTIME_PROFILE_LINKED)
  if (config->source_kind == DORT_RUNTIME_SOURCE_LINKED) {
    HMODULE module = NULL;
    if (!GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            (const wchar_t*)(const void*)OrtGetApiBase,
            &module)) {
      return dort_windows_error(
          DORT_ERROR_PLATFORM,
          "runtime_open",
          "Could not resolve the linked runtime module",
          GetLastError());
    }
    return dort_assign_windows_module(module, 0, out_library);
  }
#endif
  if (config->source_kind == DORT_RUNTIME_SOURCE_PROCESS) {
    return dort_open_windows_process(config, out_library);
  }
  if (config->source_kind == DORT_RUNTIME_SOURCE_FILE) {
    return dort_open_windows_file(config, out_library);
  }
  if (config->source_kind == DORT_RUNTIME_SOURCE_BUNDLED) {
    return dort_open_windows_bundled(config, out_library);
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
    FreeLibrary((HMODULE)library->handle);
  }
  free(library->canonical_identity_utf8);
  memset(library, 0, sizeof(*library));
}

void* dort_loader_find_optional_symbol(
    const dort_loaded_library_t* library,
    const char* symbol_name) {
  FARPROC procedure = NULL;
  void* symbol = NULL;
  if (library == NULL || library->handle == NULL || symbol_name == NULL ||
      symbol_name[0] == '\0') {
    return NULL;
  }
  procedure = GetProcAddress((HMODULE)library->handle, symbol_name);
  if (procedure != NULL) {
    memcpy(&symbol, &procedure, sizeof(symbol));
  }
  return symbol;
}

#endif /* _WIN32 */
