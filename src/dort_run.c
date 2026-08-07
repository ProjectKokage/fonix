#include "dort_internal.h"

#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <bcrypt.h>
#include <sddl.h>
#include <wchar.h>
#else
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

#define DORT_RUN_OPTIONS_MAGIC 0x44524f31u
#define DORT_RUN_RESULT_MAGIC 0x44525231u
#define DORT_MAX_CANCEL_TOKENS 1024u

struct dort_run_options {
  uint32_t magic;
  atomic_uint reference_count;
  dort_runtime_t* runtime;
  OrtRunOptions* options;
#if defined(_WIN32)
  wchar_t* profile_directory;
  wchar_t* profile_prefix;
  HANDLE profile_root_handle;
  HANDLE profile_directory_handle;
#else
  char* profile_directory;
  char* profile_prefix;
#endif
#if defined(_WIN32)
  SRWLOCK profile_lock;
#else
  pthread_mutex_t profile_lock;
#endif
};

struct dort_run_result {
  uint32_t magic;
  atomic_uint reference_count;
  dort_runtime_t* runtime;
  char** names;
  dort_value_t** values;
  size_t count;
};

typedef struct dort_cancel_entry {
  struct dort_cancel_entry* next;
  uint64_t token;
  dort_run_options_t* run_options;
  int termination_requested;
} dort_cancel_entry_t;

static dort_cancel_entry_t* dort_cancel_registry = NULL;
static size_t dort_cancel_registry_count = 0u;
static uint64_t dort_next_cancel_token = 1u;

#if defined(_WIN32)
static SRWLOCK dort_cancel_registry_lock = SRWLOCK_INIT;
static void dort_cancel_lock(void) {
  AcquireSRWLockExclusive(&dort_cancel_registry_lock);
}
static void dort_cancel_unlock(void) {
  ReleaseSRWLockExclusive(&dort_cancel_registry_lock);
}
#else
static pthread_mutex_t dort_cancel_registry_lock = PTHREAD_MUTEX_INITIALIZER;
static void dort_cancel_lock(void) {
  (void)pthread_mutex_lock(&dort_cancel_registry_lock);
}
static void dort_cancel_unlock(void) {
  (void)pthread_mutex_unlock(&dort_cancel_registry_lock);
}
#endif

static dort_cancel_entry_t*
dort_cancel_find_locked(uint64_t token, dort_cancel_entry_t*** out_link) {
  dort_cancel_entry_t** link = &dort_cancel_registry;
  while (*link != NULL) {
    if ((*link)->token == token) {
      if (out_link != NULL) {
        *out_link = link;
      }
      return *link;
    }
    link = &(*link)->next;
  }
  if (out_link != NULL) {
    *out_link = NULL;
  }
  return NULL;
}

static int dort_run_options_is_valid(const dort_run_options_t* options) {
  return options != NULL && options->magic == DORT_RUN_OPTIONS_MAGIC &&
         dort_runtime_is_valid(options->runtime) && options->options != NULL;
}

static void dort_profile_lock(dort_run_options_t* options) {
#if defined(_WIN32)
  AcquireSRWLockExclusive(&options->profile_lock);
#else
  (void)pthread_mutex_lock(&options->profile_lock);
#endif
}

static void dort_profile_unlock(dort_run_options_t* options) {
#if defined(_WIN32)
  ReleaseSRWLockExclusive(&options->profile_lock);
#else
  (void)pthread_mutex_unlock(&options->profile_lock);
#endif
}

#if defined(_WIN32)
#define DORT_WINDOWS_PROFILE_RANDOM_BYTES 16u
#define DORT_WINDOWS_PROFILE_CREATE_ATTEMPTS 16u
#define DORT_WINDOWS_PROFILE_MAX_CLEANUP_ENTRIES 4096u
#define DORT_WINDOWS_PROFILE_MAX_CLEANUP_DEPTH 16u

static int dort_windows_profile_is_separator(wchar_t value) {
  return value == L'\\' || value == L'/';
}

static int dort_windows_profile_path_is_absolute(const wchar_t* path) {
  size_t length = path == NULL ? 0u : wcslen(path);
  if (path == NULL) {
    return 0;
  }
  if (length >= 3u &&
      ((path[0] >= L'A' && path[0] <= L'Z') ||
       (path[0] >= L'a' && path[0] <= L'z')) &&
      path[1] == L':' && dort_windows_profile_is_separator(path[2])) {
    return 1;
  }
  if (length >= 3u && dort_windows_profile_is_separator(path[0]) &&
      dort_windows_profile_is_separator(path[1])) {
    return path[2] != L'?' && path[2] != L'.' && path[2] != L'\0' &&
           !dort_windows_profile_is_separator(path[2]);
  }
  return 0;
}

static int dort_windows_profile_path_is_root(const wchar_t* path) {
  size_t length = 0u;
  size_t cursor = 0u;
  if (path == NULL) {
    return 1;
  }
  length = wcslen(path);
  if (length >= 3u && path[1] == L':' &&
      dort_windows_profile_is_separator(path[2])) {
    cursor = 3u;
    while (cursor < length && dort_windows_profile_is_separator(path[cursor])) {
      ++cursor;
    }
    return cursor == length;
  }
  if (length < 5u || !dort_windows_profile_is_separator(path[0]) ||
      !dort_windows_profile_is_separator(path[1])) {
    return 1;
  }
  cursor = 2u;
  while (cursor < length && !dort_windows_profile_is_separator(path[cursor])) {
    ++cursor;
  }
  if (cursor == 2u || cursor == length) {
    return 1;
  }
  while (cursor < length && dort_windows_profile_is_separator(path[cursor])) {
    ++cursor;
  }
  {
    size_t share_start = cursor;
    while (cursor < length &&
           !dort_windows_profile_is_separator(path[cursor])) {
      ++cursor;
    }
    if (cursor == share_start) {
      return 1;
    }
  }
  while (cursor < length && dort_windows_profile_is_separator(path[cursor])) {
    ++cursor;
  }
  return cursor == length;
}

static size_t dort_windows_profile_root_length(const wchar_t* path) {
  size_t length = path == NULL ? 0u : wcslen(path);
  size_t cursor = 0u;
  if (length >= 3u && path[1] == L':' &&
      dort_windows_profile_is_separator(path[2])) {
    return 3u;
  }
  if (length < 5u || !dort_windows_profile_is_separator(path[0]) ||
      !dort_windows_profile_is_separator(path[1])) {
    return 0u;
  }
  cursor = 2u;
  while (cursor < length && !dort_windows_profile_is_separator(path[cursor])) {
    ++cursor;
  }
  while (cursor < length && dort_windows_profile_is_separator(path[cursor])) {
    ++cursor;
  }
  while (cursor < length && !dort_windows_profile_is_separator(path[cursor])) {
    ++cursor;
  }
  return cursor;
}

static int
dort_windows_profile_path_components_are_directories(const wchar_t* path) {
  size_t length = path == NULL ? 0u : wcslen(path);
  size_t root_length = dort_windows_profile_root_length(path);
  size_t cursor = root_length;
  wchar_t* traversal = NULL;
  int saw_component = 0;
  int success = 1;
  if (root_length == 0u || root_length >= length) {
    return 0;
  }
  traversal = (wchar_t*)calloc(length + 1u, sizeof(*traversal));
  if (traversal == NULL) {
    return 0;
  }
  memcpy(traversal, path, (length + 1u) * sizeof(*traversal));
  while (cursor < length) {
    size_t end = 0u;
    wchar_t saved = L'\0';
    HANDLE component = INVALID_HANDLE_VALUE;
    BY_HANDLE_FILE_INFORMATION information;
    while (cursor < length &&
           dort_windows_profile_is_separator(traversal[cursor])) {
      traversal[cursor] = L'\\';
      ++cursor;
    }
    if (cursor == length) {
      break;
    }
    end = cursor;
    while (end < length && !dort_windows_profile_is_separator(traversal[end])) {
      ++end;
    }
    saved = traversal[end];
    traversal[end] = L'\0';
    component = CreateFileW(
        traversal, FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    traversal[end] = saved;
    if (component == INVALID_HANDLE_VALUE ||
        !GetFileInformationByHandle(component, &information) ||
        (information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0u ||
        (information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0u ||
        GetFileType(component) != FILE_TYPE_DISK) {
      success = 0;
    }
    if (component != INVALID_HANDLE_VALUE && !CloseHandle(component)) {
      success = 0;
    }
    if (!success) {
      break;
    }
    saw_component = 1;
    cursor = end;
  }
  free(traversal);
  return success && saw_component;
}

static wchar_t* dort_windows_profile_utf8_to_wide(const char* value) {
  int required = 0;
  size_t ignored = 0u;
  wchar_t* result = NULL;
  if (dort_bounded_utf8_length(value, DORT_MAX_PATH_BYTES, 0, &ignored) !=
      DORT_ERROR_NONE) {
    return NULL;
  }
  required =
      MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, NULL, 0);
  if (required <= 0 || (size_t)required > DORT_MAX_PATH_BYTES) {
    return NULL;
  }
  result = (wchar_t*)calloc((size_t)required, sizeof(*result));
  if (result == NULL ||
      MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, result,
                          required) != required) {
    free(result);
    return NULL;
  }
  return result;
}

static wchar_t* dort_windows_profile_full_path(const wchar_t* path) {
  DWORD required = GetFullPathNameW(path, 0u, NULL, NULL);
  DWORD written = 0u;
  wchar_t* result = NULL;
  if (required == 0u || required >= DORT_MAX_PATH_BYTES) {
    return NULL;
  }
  result = (wchar_t*)calloc((size_t)required + 1u, sizeof(*result));
  if (result == NULL) {
    return NULL;
  }
  written = GetFullPathNameW(path, required + 1u, result, NULL);
  if (written == 0u || written > required || written >= DORT_MAX_PATH_BYTES) {
    free(result);
    return NULL;
  }
  return result;
}

static wchar_t* dort_windows_profile_long_path(const wchar_t* path) {
  DWORD required = GetLongPathNameW(path, NULL, 0u);
  DWORD written = 0u;
  wchar_t* result = NULL;
  if (required == 0u || required >= DORT_MAX_PATH_BYTES) {
    return NULL;
  }
  result = (wchar_t*)calloc((size_t)required + 1u, sizeof(*result));
  if (result == NULL) {
    return NULL;
  }
  written = GetLongPathNameW(path, result, required + 1u);
  if (written == 0u || written > required || written >= DORT_MAX_PATH_BYTES) {
    free(result);
    return NULL;
  }
  return result;
}

static wchar_t* dort_windows_profile_final_path(HANDLE handle) {
  DWORD required = GetFinalPathNameByHandleW(
      handle, NULL, 0u, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
  DWORD written = 0u;
  wchar_t* result = NULL;
  if (required == 0u || required >= DORT_MAX_PATH_BYTES) {
    return NULL;
  }
  result = (wchar_t*)calloc((size_t)required + 1u, sizeof(*result));
  if (result == NULL) {
    return NULL;
  }
  written = GetFinalPathNameByHandleW(handle, result, required + 1u,
                                      FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
  if (written == 0u || written > required || written >= DORT_MAX_PATH_BYTES) {
    free(result);
    return NULL;
  }
  return result;
}

static wchar_t*
dort_windows_profile_expected_final_path(const wchar_t* full_path) {
  static const wchar_t drive_prefix[] = L"\\\\?\\";
  static const wchar_t unc_prefix[] = L"\\\\?\\UNC\\";
  const wchar_t* prefix = drive_prefix;
  const wchar_t* source = full_path;
  size_t prefix_length = 0u;
  size_t source_length = 0u;
  size_t total = 0u;
  size_t index = 0u;
  wchar_t* result = NULL;
  if (full_path == NULL) {
    return NULL;
  }
  if (dort_windows_profile_is_separator(full_path[0]) &&
      dort_windows_profile_is_separator(full_path[1])) {
    prefix = unc_prefix;
    source = full_path + 2u;
  }
  prefix_length = wcslen(prefix);
  source_length = wcslen(source);
  while (source_length > 0u &&
         dort_windows_profile_is_separator(source[source_length - 1u])) {
    --source_length;
  }
  if (!dort_checked_add_size(prefix_length, source_length, &total) ||
      !dort_checked_add_size(total, 1u, &total) ||
      total > DORT_MAX_PATH_BYTES) {
    return NULL;
  }
  result = (wchar_t*)calloc(total, sizeof(*result));
  if (result == NULL) {
    return NULL;
  }
  memcpy(result, prefix, prefix_length * sizeof(*result));
  for (index = 0u; index < source_length; ++index) {
    wchar_t value = dort_windows_profile_is_separator(source[index])
                        ? L'\\'
                        : source[index];
    if (index == 0u && source_length >= 2u && source[1] == L':' &&
        value >= L'a' && value <= L'z') {
      value = (wchar_t)(value - (L'a' - L'A'));
    }
    result[prefix_length + index] = value;
  }
  return result;
}

static wchar_t* dort_windows_profile_join(const wchar_t* parent,
                                          const wchar_t* leaf) {
  size_t parent_length = parent == NULL ? 0u : wcslen(parent);
  size_t leaf_length = leaf == NULL ? 0u : wcslen(leaf);
  size_t needs_separator = 0u;
  size_t total = 0u;
  wchar_t* result = NULL;
  if (parent_length == 0u || leaf_length == 0u) {
    return NULL;
  }
  needs_separator =
      dort_windows_profile_is_separator(parent[parent_length - 1u]) ? 0u : 1u;
  if (!dort_checked_add_size(parent_length, needs_separator, &total) ||
      !dort_checked_add_size(total, leaf_length, &total) ||
      !dort_checked_add_size(total, 1u, &total) ||
      total > DORT_MAX_PATH_BYTES) {
    return NULL;
  }
  result = (wchar_t*)calloc(total, sizeof(*result));
  if (result == NULL) {
    return NULL;
  }
  memcpy(result, parent, parent_length * sizeof(*result));
  if (needs_separator != 0u) {
    result[parent_length] = L'\\';
  }
  memcpy(result + parent_length + needs_separator, leaf,
         (leaf_length + 1u) * sizeof(*result));
  return result;
}

static int dort_windows_profile_is_strict_child(const wchar_t* path,
                                                const wchar_t* directory) {
  size_t directory_length = directory == NULL ? 0u : wcslen(directory);
  while (directory_length > 0u &&
         dort_windows_profile_is_separator(directory[directory_length - 1u])) {
    --directory_length;
  }
  return path != NULL && directory_length > 0u &&
         wcsncmp(path, directory, directory_length) == 0 &&
         dort_windows_profile_is_separator(path[directory_length]) &&
         path[directory_length + 1u] != L'\0';
}

static int dort_windows_profile_mark_delete(HANDLE handle) {
  FILE_DISPOSITION_INFO disposition;
  memset(&disposition, 0, sizeof(disposition));
  disposition.DeleteFile = TRUE;
  return SetFileInformationByHandle(handle, FileDispositionInfo, &disposition,
                                    (DWORD)sizeof(disposition)) != 0;
}

static int dort_windows_profile_delete_tree(const wchar_t* path,
                                            const wchar_t* private_root,
                                            size_t depth, size_t* entry_count) {
  HANDLE handle = INVALID_HANDLE_VALUE;
  BY_HANDLE_FILE_INFORMATION information;
  wchar_t* canonical = NULL;
  wchar_t* search = NULL;
  WIN32_FIND_DATAW found;
  HANDLE find = INVALID_HANDLE_VALUE;
  int success = 1;
  int is_directory = 0;
  int is_reparse = 0;
  if (path == NULL || private_root == NULL || entry_count == NULL ||
      depth > DORT_WINDOWS_PROFILE_MAX_CLEANUP_DEPTH ||
      *entry_count >= DORT_WINDOWS_PROFILE_MAX_CLEANUP_ENTRIES ||
      !dort_windows_profile_is_strict_child(path, private_root)) {
    return 0;
  }
  *entry_count += 1u;
  handle = CreateFileW(
      path, DELETE | FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY,
      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
      OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
      NULL);
  if (handle == INVALID_HANDLE_VALUE ||
      !GetFileInformationByHandle(handle, &information)) {
    success = 0;
    goto windows_profile_delete_cleanup;
  }
  is_directory =
      (information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0u;
  is_reparse =
      (information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0u;
  if (!is_reparse) {
    canonical = dort_windows_profile_final_path(handle);
    if (canonical == NULL ||
        !dort_windows_profile_is_strict_child(canonical, private_root)) {
      success = 0;
      goto windows_profile_delete_cleanup;
    }
  }
  if (is_directory && !is_reparse) {
    search = dort_windows_profile_join(path, L"*");
    if (search == NULL) {
      success = 0;
      goto windows_profile_delete_cleanup;
    }
    find = FindFirstFileW(search, &found);
    if (find == INVALID_HANDLE_VALUE) {
      if (GetLastError() != ERROR_FILE_NOT_FOUND) {
        success = 0;
      }
    } else {
      do {
        wchar_t* child = NULL;
        if (wcscmp(found.cFileName, L".") == 0 ||
            wcscmp(found.cFileName, L"..") == 0) {
          continue;
        }
        child = dort_windows_profile_join(path, found.cFileName);
        if (child == NULL ||
            !dort_windows_profile_delete_tree(child, private_root, depth + 1u,
                                              entry_count)) {
          success = 0;
        }
        free(child);
      } while (FindNextFileW(find, &found) != 0);
      if (GetLastError() != ERROR_NO_MORE_FILES) {
        success = 0;
      }
    }
  }
  if (find != INVALID_HANDLE_VALUE) {
    if (!FindClose(find)) {
      success = 0;
    }
    find = INVALID_HANDLE_VALUE;
  }
  if (!dort_windows_profile_mark_delete(handle)) {
    success = 0;
  }

windows_profile_delete_cleanup:
  if (find != INVALID_HANDLE_VALUE) {
    if (!FindClose(find)) {
      success = 0;
    }
  }
  if (handle != INVALID_HANDLE_VALUE) {
    if (!CloseHandle(handle)) {
      success = 0;
    }
  }
  free(search);
  free(canonical);
  return success;
}

static int dort_windows_profile_remove_directory(const wchar_t* directory,
                                                 HANDLE* directory_handle,
                                                 HANDLE* root_handle) {
  WIN32_FIND_DATAW found;
  HANDLE find = INVALID_HANDLE_VALUE;
  wchar_t* search = NULL;
  size_t entry_count = 0u;
  int success = 1;
  if (directory != NULL) {
    search = dort_windows_profile_join(directory, L"*");
    if (search == NULL) {
      success = 0;
    } else {
      find = FindFirstFileW(search, &found);
      if (find == INVALID_HANDLE_VALUE) {
        if (GetLastError() != ERROR_FILE_NOT_FOUND &&
            GetLastError() != ERROR_PATH_NOT_FOUND) {
          success = 0;
        }
      } else {
        do {
          wchar_t* child = NULL;
          if (wcscmp(found.cFileName, L".") == 0 ||
              wcscmp(found.cFileName, L"..") == 0) {
            continue;
          }
          child = dort_windows_profile_join(directory, found.cFileName);
          if (child == NULL || !dort_windows_profile_delete_tree(
                                   child, directory, 1u, &entry_count)) {
            success = 0;
          }
          free(child);
        } while (FindNextFileW(find, &found) != 0);
        if (GetLastError() != ERROR_NO_MORE_FILES) {
          success = 0;
        }
      }
    }
  }
  if (find != INVALID_HANDLE_VALUE) {
    if (!FindClose(find)) {
      success = 0;
    }
  }
  free(search);
  if (directory_handle != NULL && *directory_handle != NULL) {
    int marked = dort_windows_profile_mark_delete(*directory_handle);
    if (!CloseHandle(*directory_handle)) {
      success = 0;
    }
    *directory_handle = NULL;
    if (!marked && directory != NULL && !RemoveDirectoryW(directory) &&
        GetLastError() != ERROR_FILE_NOT_FOUND &&
        GetLastError() != ERROR_PATH_NOT_FOUND) {
      success = 0;
    }
  } else if (directory != NULL && !RemoveDirectoryW(directory) &&
             GetLastError() != ERROR_FILE_NOT_FOUND &&
             GetLastError() != ERROR_PATH_NOT_FOUND) {
    success = 0;
  }
  if (root_handle != NULL && *root_handle != NULL) {
    if (!CloseHandle(*root_handle)) {
      success = 0;
    }
    *root_handle = NULL;
  }
  return success;
}

static int dort_windows_profile_name_is_expected(const wchar_t* name) {
  size_t length = name == NULL ? 0u : wcslen(name);
  size_t index = 0u;
  if (length <= 13u || wcsncmp(name, L"profile_", 8u) != 0 ||
      wcscmp(name + length - 5u, L".json") != 0) {
    return 0;
  }
  for (index = 8u; index < length - 5u; ++index) {
    if (name[index] < L'0' || name[index] > L'9') {
      return 0;
    }
  }
  return 1;
}

static dort_status_t* dort_profile_prepare(const char* root,
                                           wchar_t** out_directory,
                                           wchar_t** out_prefix,
                                           HANDLE* out_root_handle,
                                           HANDLE* out_directory_handle) {
  static const wchar_t private_directory_sddl[] =
      L"D:P(A;;FA;;;SY)(A;;FA;;;OW)";
  wchar_t* requested = NULL;
  wchar_t* full_root = NULL;
  wchar_t* long_root = NULL;
  wchar_t* expected_root = NULL;
  wchar_t* canonical_root = NULL;
  wchar_t* directory = NULL;
  wchar_t* canonical_directory = NULL;
  wchar_t* prefix = NULL;
  HANDLE root_handle = NULL;
  HANDLE directory_handle = NULL;
  BY_HANDLE_FILE_INFORMATION information;
  PSECURITY_DESCRIPTOR security_descriptor = NULL;
  SECURITY_ATTRIBUTES security_attributes;
  size_t attempt = 0u;
  dort_status_t* status = NULL;
  int created = 0;
  *out_directory = NULL;
  *out_prefix = NULL;
  *out_root_handle = NULL;
  *out_directory_handle = NULL;
  memset(&security_attributes, 0, sizeof(security_attributes));
  requested = dort_windows_profile_utf8_to_wide(root);
  if (requested == NULL) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_UTF8,
                              0, "run_options_profiling_start",
                              "The profiling artifact root is invalid UTF-8 or "
                              "exceeds its ABI limit.");
  }
  if (!dort_windows_profile_path_is_absolute(requested)) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PATH_NOT_ABSOLUTE, 0,
        "run_options_profiling_start",
        "The profiling artifact root must be an absolute filesystem path.");
    goto windows_profile_prepare_cleanup;
  }
  full_root = dort_windows_profile_full_path(requested);
  if (full_root == NULL || dort_windows_profile_path_is_root(full_root)) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
        "run_options_profiling_start",
        "The profiling artifact root must be an existing non-root directory.");
    goto windows_profile_prepare_cleanup;
  }
  if (!dort_windows_profile_path_components_are_directories(full_root)) {
    status =
        dort_status_create(DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT,
                           0, "run_options_profiling_start",
                           "The profiling artifact root contains a missing, "
                           "non-directory, or reparse component.");
    goto windows_profile_prepare_cleanup;
  }
  long_root = dort_windows_profile_long_path(full_root);
  if (long_root == NULL) {
    status =
        dort_status_create(DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT,
                           0, "run_options_profiling_start",
                           "The profiling artifact root must be an existing "
                           "filesystem directory.");
    goto windows_profile_prepare_cleanup;
  }
  root_handle = CreateFileW(
      long_root, FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES,
      FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
      FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
  if (root_handle == INVALID_HANDLE_VALUE) {
    root_handle = NULL;
  }
  if (root_handle == NULL ||
      !GetFileInformationByHandle(root_handle, &information) ||
      (information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0u ||
      (information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0u ||
      GetFileType(root_handle) != FILE_TYPE_DISK) {
    status =
        dort_status_create(DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT,
                           0, "run_options_profiling_start",
                           "The profiling artifact root must be an existing "
                           "non-reparse directory.");
    goto windows_profile_prepare_cleanup;
  }
  expected_root = dort_windows_profile_expected_final_path(long_root);
  canonical_root = dort_windows_profile_final_path(root_handle);
  if (expected_root == NULL || canonical_root == NULL ||
      wcscmp(expected_root, canonical_root) != 0) {
    status = dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                                DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT, 0,
                                "run_options_profiling_start",
                                "The profiling artifact root traverses a "
                                "reparse point or changed identity.");
    goto windows_profile_prepare_cleanup;
  }
  if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
          private_directory_sddl, SDDL_REVISION_1, &security_descriptor,
          NULL)) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
        "run_options_profiling_start",
        "The private profiling directory policy could not be prepared.");
    goto windows_profile_prepare_cleanup;
  }
  security_attributes.nLength = (DWORD)sizeof(security_attributes);
  security_attributes.lpSecurityDescriptor = security_descriptor;
  security_attributes.bInheritHandle = FALSE;
  for (attempt = 0u; attempt < DORT_WINDOWS_PROFILE_CREATE_ATTEMPTS;
       ++attempt) {
    UCHAR random_bytes[DORT_WINDOWS_PROFILE_RANDOM_BYTES];
    wchar_t leaf[19u + (DORT_WINDOWS_PROFILE_RANDOM_BYTES * 2u)];
    static const wchar_t hex[] = L"0123456789abcdef";
    size_t index = 0u;
    wchar_t* candidate = NULL;
    if (BCryptGenRandom(NULL, random_bytes, (ULONG)sizeof(random_bytes),
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
          "run_options_profiling_start",
          "A unique private profiling directory name could not be generated.");
      goto windows_profile_prepare_cleanup;
    }
    memcpy(leaf, L"fonix-run-profile-", 18u * sizeof(*leaf));
    for (index = 0u; index < sizeof(random_bytes); ++index) {
      leaf[18u + (index * 2u)] = hex[random_bytes[index] >> 4u];
      leaf[19u + (index * 2u)] = hex[random_bytes[index] & 0x0fu];
    }
    leaf[18u + (sizeof(random_bytes) * 2u)] = L'\0';
    candidate = dort_windows_profile_join(canonical_root, leaf);
    if (candidate == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
          "run_options_profiling_start",
          "The private profiling directory path could not be allocated.");
      goto windows_profile_prepare_cleanup;
    }
    if (CreateDirectoryW(candidate, &security_attributes)) {
      directory = candidate;
      created = 1;
      break;
    }
    free(candidate);
    if (GetLastError() != ERROR_ALREADY_EXISTS &&
        GetLastError() != ERROR_FILE_EXISTS) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
          "run_options_profiling_start",
          "The private profiling directory could not be created.");
      goto windows_profile_prepare_cleanup;
    }
  }
  if (!created || directory == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
        "run_options_profiling_start",
        "A unique private profiling directory could not be created.");
    goto windows_profile_prepare_cleanup;
  }
  directory_handle = CreateFileW(
      directory,
      FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | READ_CONTROL | DELETE,
      FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
      FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
  if (directory_handle == INVALID_HANDLE_VALUE) {
    directory_handle = NULL;
  }
  if (directory_handle == NULL ||
      !GetFileInformationByHandle(directory_handle, &information) ||
      (information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0u ||
      (information.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0u ||
      GetFileType(directory_handle) != FILE_TYPE_DISK) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
        "run_options_profiling_start",
        "The private profiling directory could not be retained safely.");
    goto windows_profile_prepare_cleanup;
  }
  canonical_directory = dort_windows_profile_final_path(directory_handle);
  if (canonical_directory == NULL ||
      wcscmp(canonical_directory, directory) != 0 ||
      !dort_windows_profile_is_strict_child(canonical_directory,
                                            canonical_root)) {
    status = dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                                DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT, 0,
                                "run_options_profiling_start",
                                "The private profiling directory changed "
                                "identity or escaped its root.");
    goto windows_profile_prepare_cleanup;
  }
  prefix = dort_windows_profile_join(canonical_directory, L"profile");
  if (prefix == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
        "run_options_profiling_start",
        "The unique profiling prefix could not be allocated.");
    goto windows_profile_prepare_cleanup;
  }
  free(directory);
  directory = canonical_directory;
  canonical_directory = NULL;
  *out_directory = directory;
  *out_prefix = prefix;
  *out_root_handle = root_handle;
  *out_directory_handle = directory_handle;
  directory = NULL;
  prefix = NULL;
  root_handle = NULL;
  directory_handle = NULL;

windows_profile_prepare_cleanup:
  if (status != NULL && created && directory != NULL) {
    (void)dort_windows_profile_remove_directory(directory, &directory_handle,
                                                &root_handle);
  }
  if (directory_handle != NULL) {
    CloseHandle(directory_handle);
  }
  if (root_handle != NULL) {
    CloseHandle(root_handle);
  }
  if (security_descriptor != NULL) {
    LocalFree(security_descriptor);
  }
  free(prefix);
  free(canonical_directory);
  free(directory);
  free(canonical_root);
  free(expected_root);
  free(long_root);
  free(full_root);
  free(requested);
  return status;
}

static dort_status_t*
dort_profile_read_and_remove(const wchar_t* directory, HANDLE* root_handle,
                             HANDLE* directory_handle,
                             dort_string_t* out_profile_json) {
  wchar_t* search = NULL;
  wchar_t* expected_name = NULL;
  wchar_t* expected_path = NULL;
  wchar_t* canonical_file = NULL;
  WIN32_FIND_DATAW found;
  HANDLE find = INVALID_HANDLE_VALUE;
  HANDLE file = INVALID_HANDLE_VALUE;
  BY_HANDLE_FILE_INFORMATION information;
  LARGE_INTEGER first_size;
  LARGE_INTEGER final_size;
  char* bytes = NULL;
  size_t expected_length = 0u;
  size_t offset = 0u;
  size_t candidate_count = 0u;
  size_t utf8_length = 0u;
  int invalid_entry = 0;
  int cleanup_success = 0;
  int handles_closed = 1;
  dort_status_t* status = NULL;
  search = dort_windows_profile_join(directory, L"*");
  if (search == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
        "run_options_profiling_finish",
        "The private profiling directory search could not be allocated.");
    goto windows_profile_read_cleanup;
  }
  find = FindFirstFileW(search, &found);
  if (find != INVALID_HANDLE_VALUE) {
    do {
      size_t name_length = 0u;
      if (wcscmp(found.cFileName, L".") == 0 ||
          wcscmp(found.cFileName, L"..") == 0) {
        continue;
      }
      candidate_count += 1u;
      if (candidate_count > 1u ||
          !dort_windows_profile_name_is_expected(found.cFileName) ||
          (found.dwFileAttributes &
           (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) != 0u) {
        invalid_entry = 1;
        continue;
      }
      name_length = wcslen(found.cFileName);
      expected_name =
          (wchar_t*)calloc(name_length + 1u, sizeof(*expected_name));
      if (expected_name == NULL) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
            "run_options_profiling_finish",
            "The profile filename could not be copied.");
        goto windows_profile_read_cleanup;
      }
      memcpy(expected_name, found.cFileName,
             (name_length + 1u) * sizeof(*expected_name));
    } while (FindNextFileW(find, &found) != 0);
    if (GetLastError() != ERROR_NO_MORE_FILES) {
      status = dort_status_create(DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM,
                                  0, "run_options_profiling_finish",
                                  "The private profiling directory could not "
                                  "be enumerated completely.");
      goto windows_profile_read_cleanup;
    }
  } else if (GetLastError() != ERROR_FILE_NOT_FOUND) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
        "run_options_profiling_finish",
        "The private profiling directory could not be enumerated.");
    goto windows_profile_read_cleanup;
  }
  if (invalid_entry || candidate_count != 1u || expected_name == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_MODEL_INVALID, 0,
        "run_options_profiling_finish",
        "ONNX Runtime did not produce exactly one closed profile artifact.");
    goto windows_profile_read_cleanup;
  }
  expected_path = dort_windows_profile_join(directory, expected_name);
  if (expected_path == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
        "run_options_profiling_finish",
        "The profile artifact path could not be allocated.");
    goto windows_profile_read_cleanup;
  }
  file = CreateFileW(
      expected_path, GENERIC_READ | DELETE | FILE_READ_ATTRIBUTES,
      FILE_SHARE_READ, NULL, OPEN_EXISTING,
      FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
  if (file == INVALID_HANDLE_VALUE ||
      !GetFileInformationByHandle(file, &information) ||
      (information.dwFileAttributes &
       (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) != 0u ||
      information.nNumberOfLinks != 1u || GetFileType(file) != FILE_TYPE_DISK) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_MODEL_INVALID, 0,
        "run_options_profiling_finish",
        "The run profile is not one regular non-reparse disk file.");
    goto windows_profile_read_cleanup;
  }
  canonical_file = dort_windows_profile_final_path(file);
  if (canonical_file == NULL || wcscmp(canonical_file, expected_path) != 0 ||
      !dort_windows_profile_is_strict_child(canonical_file, directory)) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT, 0,
        "run_options_profiling_finish",
        "The run profile changed identity or escaped its private directory.");
    goto windows_profile_read_cleanup;
  }
  if (!GetFileSizeEx(file, &first_size) || first_size.QuadPart <= 0 ||
      (uint64_t)first_size.QuadPart >
          (uint64_t)DORT_MAX_PROVIDER_PROFILE_BYTES) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_LIMIT_EXCEEDED, 0,
        "run_options_profiling_finish",
        "The run profile is empty or exceeds its byte limit.");
    goto windows_profile_read_cleanup;
  }
  expected_length = (size_t)first_size.QuadPart;
  bytes = (char*)malloc(expected_length + 1u);
  if (bytes == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
        "run_options_profiling_finish",
        "The bounded run profile could not be allocated.");
    goto windows_profile_read_cleanup;
  }
  while (offset < expected_length) {
    DWORD requested = (DWORD)((expected_length - offset) > UINT32_MAX
                                  ? UINT32_MAX
                                  : (expected_length - offset));
    DWORD count = 0u;
    if (!ReadFile(file, bytes + offset, requested, &count, NULL) ||
        count == 0u) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
          "run_options_profiling_finish",
          "The bounded run profile could not be read completely.");
      goto windows_profile_read_cleanup;
    }
    offset += (size_t)count;
  }
  {
    char extra = '\0';
    DWORD count = 0u;
    if (!ReadFile(file, &extra, 1u, &count, NULL) || count != 0u ||
        !GetFileSizeEx(file, &final_size) ||
        final_size.QuadPart != first_size.QuadPart) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_MODEL_INVALID, 0,
          "run_options_profiling_finish",
          "The run profile changed size during its bounded read.");
      goto windows_profile_read_cleanup;
    }
  }
  bytes[expected_length] = '\0';
  if (dort_bounded_utf8_length(bytes, DORT_MAX_PROVIDER_PROFILE_BYTES, 0,
                               &utf8_length) != DORT_ERROR_NONE ||
      utf8_length != expected_length) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_UTF8, 0,
        "run_options_profiling_finish",
        "The run profile is not closed non-empty UTF-8 JSON bytes.");
    goto windows_profile_read_cleanup;
  }
  if (!dort_windows_profile_mark_delete(file)) {
    status =
        dort_status_create(DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
                           "run_options_profiling_finish",
                           "The run profile could not be retired securely.");
    goto windows_profile_read_cleanup;
  }
windows_profile_read_cleanup:
  if (find != INVALID_HANDLE_VALUE) {
    if (!FindClose(find)) {
      handles_closed = 0;
    }
  }
  if (file != INVALID_HANDLE_VALUE) {
    if (!CloseHandle(file)) {
      handles_closed = 0;
    }
  }
  cleanup_success = dort_windows_profile_remove_directory(
      directory, directory_handle, root_handle);
  if (!handles_closed) {
    cleanup_success = 0;
  }
  if (status == NULL && !cleanup_success) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
        "run_options_profiling_finish",
        "The private profiling artifacts could not be retired completely.");
  }
  if (status == NULL) {
    out_profile_json->data = (const uint8_t*)bytes;
    out_profile_json->length = expected_length;
    out_profile_json->private_owner = bytes;
    bytes = NULL;
  }
  free(bytes);
  free(canonical_file);
  free(expected_path);
  free(expected_name);
  free(search);
  return status;
}

#if defined(FONIX_WINDOWS_PROFILE_TESTING)
dort_status_t* dort_windows_test_profile_prepare(const char* root,
                                                 wchar_t** out_directory,
                                                 wchar_t** out_prefix,
                                                 HANDLE* out_root_handle,
                                                 HANDLE* out_directory_handle) {
  return dort_profile_prepare(root, out_directory, out_prefix, out_root_handle,
                              out_directory_handle);
}

dort_status_t* dort_windows_test_profile_read_and_remove(
    const wchar_t* directory, HANDLE* root_handle, HANDLE* directory_handle,
    dort_string_t* out_profile_json) {
  return dort_profile_read_and_remove(directory, root_handle, directory_handle,
                                      out_profile_json);
}

int dort_windows_test_profile_remove_directory(const wchar_t* directory,
                                               HANDLE* directory_handle,
                                               HANDLE* root_handle) {
  return dort_windows_profile_remove_directory(directory, directory_handle,
                                               root_handle);
}
#endif

#else
static int dort_profile_name_is_expected(const char* name) {
  size_t length = name == NULL ? 0u : strlen(name);
  return length > 13u && strncmp(name, "profile_", 8u) == 0 &&
         strcmp(name + length - 5u, ".json") == 0;
}

static void dort_profile_remove_directory(const char* directory) {
  DIR* stream = NULL;
  struct dirent* entry = NULL;
  int directory_fd = -1;
  if (directory == NULL) {
    return;
  }
  directory_fd = open(directory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
  if (directory_fd >= 0) {
    stream = fdopendir(directory_fd);
    if (stream != NULL) {
      while ((entry = readdir(stream)) != NULL) {
        if (strcmp(entry->d_name, ".") != 0 &&
            strcmp(entry->d_name, "..") != 0) {
          (void)unlinkat(directory_fd, entry->d_name, 0);
        }
      }
      (void)closedir(stream);
      directory_fd = -1;
    }
  }
  if (directory_fd >= 0) {
    (void)close(directory_fd);
  }
  (void)rmdir(directory);
}

static dort_status_t* dort_profile_prepare(const char* root,
                                           char** out_directory,
                                           char** out_prefix) {
  struct stat root_stat;
  char* canonical_root = NULL;
  char* directory = NULL;
  char* prefix = NULL;
  size_t root_length = 0u;
  size_t directory_length = 0u;
  int validation = DORT_ERROR_NONE;
  *out_directory = NULL;
  *out_prefix = NULL;
  validation =
      dort_bounded_utf8_length(root, DORT_MAX_PATH_BYTES, 0, &root_length);
  if (validation != DORT_ERROR_NONE || root[0] != '/' ||
      strcmp(root, "/") == 0 || lstat(root, &root_stat) != 0 ||
      S_ISLNK(root_stat.st_mode) || !S_ISDIR(root_stat.st_mode)) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              validation == DORT_ERROR_NONE
                                  ? DORT_ERROR_INVALID_ARGUMENT
                                  : validation,
                              0, "run_options_profiling_start",
                              "The profiling artifact root must be an existing "
                              "non-symlink directory.");
  }
  canonical_root = realpath(root, NULL);
  if (canonical_root == NULL || strlen(canonical_root) > DORT_MAX_PATH_BYTES) {
    free(canonical_root);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT, 0,
        "run_options_profiling_start",
        "The profiling artifact root could not be resolved safely.");
  }
  root_length = strlen(canonical_root);
  if (root_length > DORT_MAX_PATH_BYTES - 25u) {
    free(canonical_root);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_LIMIT_EXCEEDED, 0,
        "run_options_profiling_start",
        "The profiling artifact path exceeds its ABI limit.");
  }
  directory_length = root_length + 25u;
  directory = (char*)malloc(directory_length + 1u);
  if (directory != NULL) {
    (void)snprintf(directory, directory_length + 1u,
                   "%s/fonix-run-profile-XXXXXX", canonical_root);
  }
  free(canonical_root);
  if (directory == NULL || mkdtemp(directory) == NULL) {
    free(directory);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        directory == NULL ? DORT_ERROR_ALLOCATION_FAILED : DORT_ERROR_PLATFORM,
        0, "run_options_profiling_start",
        "The private profiling directory could not be created.");
  }
  prefix = (char*)malloc(strlen(directory) + 9u);
  if (prefix == NULL) {
    dort_profile_remove_directory(directory);
    free(directory);
    return dort_status_create(DORT_ERROR_DOMAIN_ALLOCATION,
                              DORT_ERROR_ALLOCATION_FAILED, 0,
                              "run_options_profiling_start",
                              "The profiling prefix could not be allocated.");
  }
  (void)snprintf(prefix, strlen(directory) + 9u, "%s/profile", directory);
  *out_directory = directory;
  *out_prefix = prefix;
  return NULL;
}

static dort_status_t*
dort_profile_read_and_remove(const char* directory,
                             dort_string_t* out_profile_json) {
  DIR* stream = NULL;
  struct dirent* entry = NULL;
  struct stat file_stat;
  int directory_fd = -1;
  int file_fd = -1;
  char* expected_name = NULL;
  char* bytes = NULL;
  size_t offset = 0u;
  size_t expected_length = 0u;
  size_t candidate_count = 0u;
  int invalid_entry = 0;
  dort_status_t* status = NULL;
  directory_fd = open(directory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
  if (directory_fd < 0) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
        "run_options_profiling_finish",
        "The private profiling directory could not be inspected.");
    goto profile_read_cleanup;
  }
  stream = fdopendir(dup(directory_fd));
  if (stream == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
        "run_options_profiling_finish",
        "The private profiling directory could not be enumerated.");
    goto profile_read_cleanup;
  }
  while ((entry = readdir(stream)) != NULL) {
    if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) {
      continue;
    }
    candidate_count += 1u;
    if (candidate_count > 1u || !dort_profile_name_is_expected(entry->d_name)) {
      invalid_entry = 1;
      continue;
    }
    expected_name = dort_copy_c_string(entry->d_name, strlen(entry->d_name));
    if (expected_name == NULL) {
      status = dort_status_create(DORT_ERROR_DOMAIN_ALLOCATION,
                                  DORT_ERROR_ALLOCATION_FAILED, 0,
                                  "run_options_profiling_finish",
                                  "The profile filename could not be copied.");
      goto profile_read_cleanup;
    }
  }
  (void)closedir(stream);
  stream = NULL;
  if (invalid_entry || candidate_count != 1u || expected_name == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_MODEL_INVALID, 0,
        "run_options_profiling_finish",
        "ONNX Runtime did not produce exactly one closed profile artifact.");
    goto profile_read_cleanup;
  }
  file_fd = openat(directory_fd, expected_name, O_RDONLY | O_NOFOLLOW);
  if (file_fd < 0 || fstat(file_fd, &file_stat) != 0 ||
      !S_ISREG(file_stat.st_mode) || file_stat.st_size <= 0 ||
      (uint64_t)file_stat.st_size > (uint64_t)DORT_MAX_PROVIDER_PROFILE_BYTES) {
    status =
        dort_status_create(DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_LIMIT_EXCEEDED, 0,
                           "run_options_profiling_finish",
                           "The run profile is missing, non-regular, empty, or "
                           "exceeds its byte limit.");
    goto profile_read_cleanup;
  }
  expected_length = (size_t)file_stat.st_size;
  bytes = (char*)malloc(expected_length + 1u);
  if (bytes == NULL) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
        "run_options_profiling_finish",
        "The bounded run profile could not be allocated.");
    goto profile_read_cleanup;
  }
  while (offset < expected_length) {
    ssize_t count = read(file_fd, bytes + offset, expected_length - offset);
    if (count <= 0) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0,
          "run_options_profiling_finish",
          "The bounded run profile could not be read completely.");
      goto profile_read_cleanup;
    }
    offset += (size_t)count;
  }
  if (memchr(bytes, '\0', expected_length) != NULL) {
    status =
        dort_status_create(DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_UTF8, 0,
                           "run_options_profiling_finish",
                           "The run profile contains an embedded null byte.");
    goto profile_read_cleanup;
  }
  bytes[expected_length] = '\0';
  memset(out_profile_json, 0, sizeof(*out_profile_json));
  out_profile_json->struct_size = (uint32_t)sizeof(*out_profile_json);
  out_profile_json->data = (const uint8_t*)bytes;
  out_profile_json->length = expected_length;
  out_profile_json->private_owner = bytes;
  bytes = NULL;

profile_read_cleanup:
  if (stream != NULL) {
    (void)closedir(stream);
  }
  if (file_fd >= 0) {
    (void)close(file_fd);
  }
  if (directory_fd >= 0) {
    if (expected_name != NULL) {
      (void)unlinkat(directory_fd, expected_name, 0);
    }
    (void)close(directory_fd);
  }
  free(expected_name);
  free(bytes);
  dort_profile_remove_directory(directory);
  return status;
}
#endif

static int dort_run_result_is_valid(const dort_run_result_t* result) {
  return result != NULL && result->magic == DORT_RUN_RESULT_MAGIC &&
         dort_runtime_is_valid(result->runtime) &&
         (result->count == 0u ||
          (result->names != NULL && result->values != NULL));
}

dort_status_t* DORT_CALL dort_run_options_create(
    dort_runtime_t* runtime, dort_run_options_t** out_options) {
  dort_run_options_t* options = NULL;
  OrtRunOptions* ort_options = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  if (out_options == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
        "run_options_create", "The output run-options pointer is null.");
  }
  *out_options = NULL;
  if (!dort_runtime_is_valid(runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
        "run_options_create", "The runtime handle is null or invalid.");
  }
  status = dort_runtime_require_api(runtime, DORT_API_FEATURE_RUN,
                                    "run_options_create");
  if (status != NULL) {
    return status;
  }
  ort_status = dort_runtime_api(runtime)->CreateRunOptions(&ort_options);
  if (ort_status != NULL || ort_options == NULL) {
    if (ort_options != NULL) {
      dort_runtime_api(runtime)->ReleaseRunOptions(ort_options);
    }
    return ort_status == NULL
               ? dort_status_create(DORT_ERROR_DOMAIN_ORT_API,
                                    DORT_ERROR_ORT_API_UNAVAILABLE, 0,
                                    "run_options_create",
                                    "ONNX Runtime did not create run options.")
               : dort_status_from_ort(runtime, ort_status,
                                      DORT_ERROR_RUN_FAILED,
                                      "run_options_create");
  }
  options = (dort_run_options_t*)calloc(1u, sizeof(*options));
  if (options == NULL) {
    dort_runtime_api(runtime)->ReleaseRunOptions(ort_options);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
        "run_options_create", "Could not allocate the run-options handle.");
  }
  dort_runtime_retain(runtime);
  options->magic = DORT_RUN_OPTIONS_MAGIC;
  atomic_init(&options->reference_count, 1u);
  options->runtime = runtime;
  options->options = ort_options;
#if defined(_WIN32)
  InitializeSRWLock(&options->profile_lock);
#else
  if (pthread_mutex_init(&options->profile_lock, NULL) != 0) {
    options->magic = 0u;
    options->runtime = NULL;
    options->options = NULL;
    dort_runtime_api(runtime)->ReleaseRunOptions(ort_options);
    free(options);
    dort_runtime_release(runtime);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_PLATFORM, 0, "run_options_create",
        "Could not initialize the profiling ownership lock.");
  }
#endif
  *out_options = options;
  return NULL;
}

void DORT_CALL dort_run_options_retain(dort_run_options_t* options) {
  unsigned int references = 0u;
  if (!dort_run_options_is_valid(options)) {
    return;
  }
  references =
      atomic_load_explicit(&options->reference_count, memory_order_relaxed);
  while (references != 0u && references != UINT32_MAX) {
    if (atomic_compare_exchange_weak_explicit(
            &options->reference_count, &references, references + 1u,
            memory_order_relaxed, memory_order_relaxed)) {
      return;
    }
  }
}

void DORT_CALL dort_run_options_release(dort_run_options_t* options) {
  unsigned int previous = 0u;
  if (!dort_run_options_is_valid(options)) {
    return;
  }
  previous = atomic_fetch_sub_explicit(&options->reference_count, 1u,
                                       memory_order_acq_rel);
  if (previous == 1u) {
    dort_runtime_t* runtime = options->runtime;
    OrtRunOptions* ort_options = options->options;
#if defined(_WIN32)
    wchar_t* profile_directory = NULL;
    wchar_t* profile_prefix = NULL;
    HANDLE profile_root_handle = NULL;
    HANDLE profile_directory_handle = NULL;
#else
    char* profile_directory = NULL;
    char* profile_prefix = NULL;
#endif
    dort_profile_lock(options);
    profile_directory = options->profile_directory;
    profile_prefix = options->profile_prefix;
    options->profile_directory = NULL;
    options->profile_prefix = NULL;
#if defined(_WIN32)
    profile_root_handle = options->profile_root_handle;
    profile_directory_handle = options->profile_directory_handle;
    options->profile_root_handle = NULL;
    options->profile_directory_handle = NULL;
#endif
    if (profile_directory != NULL) {
      OrtStatus* ignored =
          dort_runtime_api(runtime)->RunOptionsDisableProfiling(ort_options);
      if (ignored != NULL) {
        dort_runtime_api(runtime)->ReleaseStatus(ignored);
      }
    }
#if defined(_WIN32)
    (void)dort_windows_profile_remove_directory(
        profile_directory, &profile_directory_handle, &profile_root_handle);
#else
    dort_profile_remove_directory(profile_directory);
#endif
    free(profile_directory);
    free(profile_prefix);
    dort_profile_unlock(options);
#if !defined(_WIN32)
    (void)pthread_mutex_destroy(&options->profile_lock);
#endif
    options->magic = 0u;
    options->runtime = NULL;
    options->options = NULL;
    dort_runtime_api(runtime)->ReleaseRunOptions(ort_options);
    free(options);
    dort_runtime_release(runtime);
  }
}

static dort_status_t* dort_set_terminate(dort_run_options_t* options,
                                         int terminate) {
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  const char* operation =
      terminate ? "run_options_set_terminate" : "run_options_unset_terminate";
  if (!dort_run_options_is_valid(options)) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0, operation,
                              "The run-options handle is null or invalid.");
  }
  status = dort_runtime_require_api(options->runtime, DORT_API_FEATURE_RUN,
                                    operation);
  if (status != NULL) {
    return status;
  }
  ort_status = terminate ? dort_runtime_api(options->runtime)
                               ->RunOptionsSetTerminate(options->options)
                         : dort_runtime_api(options->runtime)
                               ->RunOptionsUnsetTerminate(options->options);
  return dort_status_from_ort(options->runtime, ort_status,
                              DORT_ERROR_RUN_FAILED, operation);
}

dort_status_t* DORT_CALL
dort_run_options_set_terminate(dort_run_options_t* options) {
  return dort_set_terminate(options, 1);
}

dort_status_t* DORT_CALL
dort_run_options_unset_terminate(dort_run_options_t* options) {
  return dort_set_terminate(options, 0);
}

dort_status_t* DORT_CALL dort_run_options_profiling_start(
    dort_run_options_t* options, const char* artifact_root_utf8) {
  dort_status_t* status = NULL;
  OrtStatus* ort_status = NULL;
#if defined(_WIN32)
  wchar_t* directory = NULL;
  wchar_t* prefix = NULL;
  HANDLE root_handle = NULL;
  HANDLE directory_handle = NULL;
#else
  char* directory = NULL;
  char* prefix = NULL;
#endif
  if (!dort_run_options_is_valid(options)) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0,
                              "run_options_profiling_start",
                              "The run-options handle is null or invalid.");
  }
  status =
      dort_runtime_require_api(options->runtime, DORT_API_FEATURE_RUN_PROFILING,
                               "run_options_profiling_start");
  if (status != NULL) {
    return status;
  }
#if defined(_WIN32)
  status = dort_profile_prepare(artifact_root_utf8, &directory, &prefix,
                                &root_handle, &directory_handle);
#else
  status = dort_profile_prepare(artifact_root_utf8, &directory, &prefix);
#endif
  if (status != NULL) {
    return status;
  }
  dort_profile_lock(options);
  if (options->profile_directory != NULL) {
    dort_profile_unlock(options);
#if defined(_WIN32)
    (void)dort_windows_profile_remove_directory(directory, &directory_handle,
                                                &root_handle);
#else
    dort_profile_remove_directory(directory);
#endif
    free(directory);
    free(prefix);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
        "run_options_profiling_start",
        "Profiling is already active on these run options.");
  }
  ort_status = dort_runtime_api(options->runtime)
                   ->RunOptionsEnableProfiling(options->options, prefix);
  if (ort_status == NULL) {
    options->profile_directory = directory;
    options->profile_prefix = prefix;
#if defined(_WIN32)
    options->profile_root_handle = root_handle;
    options->profile_directory_handle = directory_handle;
    root_handle = NULL;
    directory_handle = NULL;
#endif
    directory = NULL;
    prefix = NULL;
  }
  dort_profile_unlock(options);
  if (ort_status != NULL) {
    status = dort_status_from_ort(options->runtime, ort_status,
                                  DORT_ERROR_RUN_FAILED,
                                  "run_options_profiling_start");
  }
#if defined(_WIN32)
  (void)dort_windows_profile_remove_directory(directory, &directory_handle,
                                              &root_handle);
#else
  dort_profile_remove_directory(directory);
#endif
  free(directory);
  free(prefix);
  return status;
}

dort_status_t* DORT_CALL dort_run_options_profiling_finish(
    dort_run_options_t* options, dort_string_t* out_profile_json) {
  dort_status_t* status = NULL;
  OrtStatus* ort_status = NULL;
#if defined(_WIN32)
  wchar_t* directory = NULL;
  wchar_t* prefix = NULL;
  HANDLE root_handle = NULL;
  HANDLE directory_handle = NULL;
#else
  char* directory = NULL;
  char* prefix = NULL;
#endif
  if (out_profile_json == NULL) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0,
                              "run_options_profiling_finish",
                              "The output profile string pointer is null.");
  }
  memset(out_profile_json, 0, sizeof(*out_profile_json));
  out_profile_json->struct_size = (uint32_t)sizeof(*out_profile_json);
  if (!dort_run_options_is_valid(options)) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0,
                              "run_options_profiling_finish",
                              "The run-options handle is null or invalid.");
  }
  status =
      dort_runtime_require_api(options->runtime, DORT_API_FEATURE_RUN_PROFILING,
                               "run_options_profiling_finish");
  if (status != NULL) {
    return status;
  }
  dort_profile_lock(options);
  if (options->profile_directory == NULL) {
    dort_profile_unlock(options);
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0,
                              "run_options_profiling_finish",
                              "Profiling is not active on these run options.");
  }
  directory = options->profile_directory;
  prefix = options->profile_prefix;
  options->profile_directory = NULL;
  options->profile_prefix = NULL;
#if defined(_WIN32)
  root_handle = options->profile_root_handle;
  directory_handle = options->profile_directory_handle;
  options->profile_root_handle = NULL;
  options->profile_directory_handle = NULL;
#endif
  ort_status = dort_runtime_api(options->runtime)
                   ->RunOptionsDisableProfiling(options->options);
  if (ort_status != NULL) {
    status = dort_status_from_ort(options->runtime, ort_status,
                                  DORT_ERROR_RUN_FAILED,
                                  "run_options_profiling_finish");
  }
  if (status == NULL) {
#if defined(_WIN32)
    status = dort_profile_read_and_remove(directory, &root_handle,
                                          &directory_handle, out_profile_json);
#else
    status = dort_profile_read_and_remove(directory, out_profile_json);
#endif
  } else {
#if defined(_WIN32)
    (void)dort_windows_profile_remove_directory(directory, &directory_handle,
                                                &root_handle);
#else
    dort_profile_remove_directory(directory);
#endif
  }
  free(directory);
  free(prefix);
  dort_profile_unlock(options);
  return status;
}

dort_status_t* DORT_CALL dort_cancel_token_register(
    dort_run_options_t* run_options, uint64_t* out_token) {
  dort_cancel_entry_t* entry = NULL;
  dort_cancel_entry_t* current = NULL;
  uint64_t token = 0u;
  size_t attempts = 0u;
  if (out_token == NULL) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0,
                              "cancel_token_register",
                              "The output cancellation token pointer is null.");
  }
  *out_token = 0u;
  if (!dort_run_options_is_valid(run_options)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
        "cancel_token_register", "The run-options handle is null or invalid.");
  }
  entry = (dort_cancel_entry_t*)calloc(1u, sizeof(*entry));
  if (entry == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
        "cancel_token_register",
        "Could not allocate a bounded cancellation entry.");
  }

  dort_cancel_lock();
  if (dort_cancel_registry_count >= DORT_MAX_CANCEL_TOKENS) {
    dort_cancel_unlock();
    free(entry);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_CANCEL_REGISTRY_FULL, 0,
        "cancel_token_register", "The process cancellation registry is full.");
  }
  for (current = dort_cancel_registry; current != NULL;
       current = current->next) {
    if (current->run_options == run_options) {
      dort_cancel_unlock();
      free(entry);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
          "cancel_token_register",
          "The run-options handle already has an active cancellation token.");
    }
  }
  for (attempts = 0u; attempts <= DORT_MAX_CANCEL_TOKENS; ++attempts) {
    token = dort_next_cancel_token;
    dort_next_cancel_token = dort_next_cancel_token == (uint64_t)INT64_MAX
                                 ? 1u
                                 : dort_next_cancel_token + 1u;
    if (token != 0u && dort_cancel_find_locked(token, NULL) == NULL) {
      break;
    }
  }
  if (token == 0u || dort_cancel_find_locked(token, NULL) != NULL) {
    dort_cancel_unlock();
    free(entry);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_CANCEL_REGISTRY_FULL, 0,
        "cancel_token_register",
        "Could not allocate a unique bounded cancellation token.");
  }
  dort_run_options_retain(run_options);
  entry->token = token;
  entry->run_options = run_options;
  entry->next = dort_cancel_registry;
  dort_cancel_registry = entry;
  ++dort_cancel_registry_count;
  *out_token = token;
  dort_cancel_unlock();
  return NULL;
}

dort_status_t* DORT_CALL dort_cancel_token_request(uint64_t token,
                                                   uint32_t* out_did_request) {
  dort_cancel_entry_t* entry = NULL;
  dort_status_t* status = NULL;
  if (out_did_request == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
        "cancel_token_request",
        "The output cancellation-request pointer is null.");
  }
  *out_did_request = 0u;
  if (token == 0u || token > (uint64_t)INT64_MAX) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_CANCEL_TOKEN_UNKNOWN, 0,
        "cancel_token_request",
        "The cancellation token is unknown or already finished.");
  }
  dort_cancel_lock();
  entry = dort_cancel_find_locked(token, NULL);
  if (entry == NULL) {
    dort_cancel_unlock();
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_CANCEL_TOKEN_UNKNOWN, 0,
        "cancel_token_request",
        "The cancellation token is unknown or already finished.");
  }
  if (!entry->termination_requested) {
    status = dort_set_terminate(entry->run_options, 1);
    if (status == NULL) {
      entry->termination_requested = 1;
      *out_did_request = 1u;
    }
  }
  dort_cancel_unlock();
  return status;
}

dort_status_t* DORT_CALL dort_cancel_token_finish(uint64_t token,
                                                  uint32_t* out_was_requested) {
  dort_cancel_entry_t* entry = NULL;
  dort_cancel_entry_t** link = NULL;
  dort_status_t* status = NULL;
  if (out_was_requested == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
        "cancel_token_finish",
        "The output cancellation-settlement pointer is null.");
  }
  *out_was_requested = 0u;
  if (token == 0u || token > (uint64_t)INT64_MAX) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_CANCEL_TOKEN_UNKNOWN, 0,
        "cancel_token_finish",
        "The cancellation token is unknown or already finished.");
  }
  dort_cancel_lock();
  entry = dort_cancel_find_locked(token, &link);
  if (entry == NULL || link == NULL) {
    dort_cancel_unlock();
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_CANCEL_TOKEN_UNKNOWN, 0,
        "cancel_token_finish",
        "The cancellation token is unknown or already finished.");
  }
  if (entry->termination_requested) {
    status = dort_set_terminate(entry->run_options, 0);
    if (status != NULL) {
      dort_cancel_unlock();
      return status;
    }
  }
  *out_was_requested = entry->termination_requested ? 1u : 0u;
  *link = entry->next;
  --dort_cancel_registry_count;
  dort_cancel_unlock();
  dort_run_options_release(entry->run_options);
  free(entry);
  return NULL;
}

static int dort_find_name_index(const char* name, const dort_session_t* session,
                                int inputs, size_t* out_index) {
  size_t count = inputs ? dort_session_internal_input_count(session)
                        : dort_session_internal_output_count(session);
  size_t index = 0u;
  for (index = 0u; index < count; ++index) {
    const char* candidate =
        inputs ? dort_session_internal_input_name(session, index)
               : dort_session_internal_output_name(session, index);
    if (candidate != NULL && strcmp(candidate, name) == 0) {
      if (out_index != NULL) {
        *out_index = index;
      }
      return 1;
    }
  }
  return 0;
}

static void dort_run_result_destroy(dort_run_result_t* result) {
  size_t index = 0u;
  dort_runtime_t* runtime = NULL;
  if (result == NULL) {
    return;
  }
  runtime = result->runtime;
  for (index = 0u; index < result->count; ++index) {
    free(result->names == NULL ? NULL : result->names[index]);
    dort_value_release(result->values == NULL ? NULL : result->values[index]);
  }
  free(result->names);
  free(result->values);
  result->magic = 0u;
  result->runtime = NULL;
  free(result);
  dort_runtime_release(runtime);
}

dort_status_t* DORT_CALL dort_session_run(dort_session_t* session,
                                          dort_run_options_t* run_options,
                                          const dort_named_value_t* inputs,
                                          size_t input_count,
                                          const char* const* output_names_utf8,
                                          size_t output_count,
                                          dort_run_result_t** out_result) {
  dort_runtime_t* runtime = NULL;
  const OrtApi* api = NULL;
  const char** input_names = NULL;
  const OrtValue** input_values = NULL;
  OrtValue** outputs = NULL;
  dort_run_result_t* result = NULL;
  dort_status_t* status = NULL;
  OrtStatus* ort_status = NULL;
  size_t actual_input_count = 0u;
  size_t retained_input_count = 0u;
  size_t index = 0u;
  int session_retained = 0;

  if (out_result == NULL) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0, "session_run",
                              "The output run-result pointer is null.");
  }
  *out_result = NULL;
  if (!dort_session_internal_is_valid(session)) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0, "session_run",
                              "The session handle is null or invalid.");
  }
  runtime = dort_session_internal_runtime(session);
  if (run_options != NULL && !dort_run_options_is_valid(run_options)) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0, "session_run",
                              "The run-options handle is invalid.");
  }
  if (run_options != NULL &&
      !dort_runtime_has_same_identity(runtime, run_options->runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_RUNTIME_IDENTITY_MISMATCH, 0,
        "session_run",
        "The run options belong to a different runtime identity.");
  }
  if (input_count > dort_session_internal_input_count(session) ||
      input_count > DORT_MAX_IO_COUNT ||
      (input_count == 0u) != (inputs == NULL)) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0, "session_run",
                              "The named input pointer/count is inconsistent "
                              "or exceeds the session inputs.");
  }
  if (output_count == 0u ||
      output_count > dort_session_internal_output_count(session) ||
      output_count > DORT_MAX_IO_COUNT || output_names_utf8 == NULL) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0, "session_run",
                              "The requested output pointer/count is empty, "
                              "inconsistent, or exceeds the session outputs.");
  }
  status = dort_runtime_require_api(
      runtime, DORT_API_FEATURE_RUN | DORT_API_FEATURE_COMPOSITE_VALUE,
      "session_run");
  if (status != NULL) {
    return status;
  }
  for (index = 0u; index < input_count; ++index) {
    size_t name_length = 0u;
    size_t prior = 0u;
    size_t session_index = 0u;
    uint32_t value_kind = 0u;
    uint32_t declared_kind = 0u;
    OrtValue* ort_value = NULL;
    int validation = DORT_ERROR_NONE;
    if (inputs[index].struct_size < DORT_NAMED_VALUE_V1_SIZE ||
        inputs[index].reserved0 != 0u) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0, "session_run",
          "A named input has an invalid ABI size or reserved field.");
    }
    validation = dort_bounded_utf8_length(inputs[index].name_utf8,
                                          DORT_MAX_NAME_BYTES, 0, &name_length);
    if (validation != DORT_ERROR_NONE ||
        !dort_find_name_index(inputs[index].name_utf8, session, 1,
                              &session_index)) {
      return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                                validation == DORT_ERROR_NONE
                                    ? DORT_ERROR_INVALID_ARGUMENT
                                    : validation,
                                0, "session_run",
                                "A named input has an invalid name or is not "
                                "declared by the session.");
    }
    status = dort_value_kind(inputs[index].value, &value_kind);
    if (status != NULL) {
      return status;
    }
    if (!dort_runtime_has_same_identity(
            runtime, dort_value_runtime(inputs[index].value))) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_RUNTIME_IDENTITY_MISMATCH, 0,
          "session_run",
          "An input value belongs to a different runtime identity.");
    }
    for (prior = 0u; prior < index; ++prior) {
      if (strcmp(inputs[prior].name_utf8, inputs[index].name_utf8) == 0) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
            "session_run", "The named input array contains a duplicate name.");
      }
    }
    declared_kind = dort_session_internal_input_kind(session, session_index);
    if (declared_kind != DORT_VALUE_KIND_OPTIONAL &&
        value_kind != declared_kind) {
      return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                                DORT_ERROR_VALUE_KIND_UNSUPPORTED, 0,
                                "session_run",
                                "A named input value kind does not match the "
                                "declared model input kind.");
    }
    if (value_kind == DORT_VALUE_KIND_OPTIONAL &&
        declared_kind != DORT_VALUE_KIND_OPTIONAL) {
      return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                                DORT_ERROR_VALUE_KIND_UNSUPPORTED, 0,
                                "session_run",
                                "A synthetic optional value can be supplied "
                                "only for an optional model input.");
    }
    status =
        dort_value_validate_supported(inputs[index].value, "session_run_input");
    if (status != NULL) {
      return status;
    }
    ort_value = dort_value_ort_for_run(inputs[index].value);
    if (ort_value == NULL &&
        !(declared_kind == DORT_VALUE_KIND_OPTIONAL &&
          dort_value_is_optional_none(inputs[index].value))) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0, "session_run",
          "A named input does not contain an ONNX Runtime value.");
    }
    if (ort_value != NULL) {
      ++actual_input_count;
    }
    (void)name_length;
  }
  for (index = 0u; index < dort_session_internal_input_count(session);
       ++index) {
    const char* required_name =
        dort_session_internal_input_name(session, index);
    size_t provided = 0u;
    int found = 0;
    for (provided = 0u; provided < input_count; ++provided) {
      if (strcmp(inputs[provided].name_utf8, required_name) == 0) {
        found = 1;
        break;
      }
    }
    if (!found && dort_session_internal_input_kind(session, index) !=
                      DORT_VALUE_KIND_OPTIONAL) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0, "session_run",
          "A required non-optional model input is missing.");
    }
  }
  for (index = 0u; index < output_count; ++index) {
    size_t name_length = 0u;
    size_t prior = 0u;
    int validation = dort_bounded_utf8_length(
        output_names_utf8[index], DORT_MAX_NAME_BYTES, 0, &name_length);
    if (validation != DORT_ERROR_NONE ||
        !dort_find_name_index(output_names_utf8[index], session, 0, NULL)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          validation == DORT_ERROR_NONE ? DORT_ERROR_INVALID_ARGUMENT
                                        : validation,
          0, "session_run",
          "A requested output name is invalid or not declared by the session.");
    }
    for (prior = 0u; prior < index; ++prior) {
      if (strcmp(output_names_utf8[prior], output_names_utf8[index]) == 0) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
            "session_run",
            "The requested output array contains a duplicate name.");
      }
    }
    (void)name_length;
  }

  if (actual_input_count > 0u) {
    input_names =
        (const char**)calloc(actual_input_count, sizeof(*input_names));
    input_values =
        (const OrtValue**)calloc(actual_input_count, sizeof(*input_values));
  }
  outputs = (OrtValue**)calloc(output_count, sizeof(*outputs));
  result = (dort_run_result_t*)calloc(1u, sizeof(*result));
  if (result != NULL) {
    result->names = (char**)calloc(output_count, sizeof(*result->names));
    result->values =
        (dort_value_t**)calloc(output_count, sizeof(*result->values));
  }
  if ((actual_input_count > 0u &&
       (input_names == NULL || input_values == NULL)) ||
      outputs == NULL || result == NULL || result->names == NULL ||
      result->values == NULL) {
    free(input_names);
    free(input_values);
    free(outputs);
    free(result == NULL ? NULL : result->names);
    free(result == NULL ? NULL : result->values);
    free(result);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
        "session_run", "Could not allocate bounded synchronous run vectors.");
  }
  dort_runtime_retain(runtime);
  result->magic = DORT_RUN_RESULT_MAGIC;
  atomic_init(&result->reference_count, 1u);
  result->runtime = runtime;
  result->count = output_count;
  for (index = 0u; index < output_count; ++index) {
    result->names[index] = dort_copy_c_string(output_names_utf8[index],
                                              strlen(output_names_utf8[index]));
    if (result->names[index] == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION, DORT_ERROR_ALLOCATION_FAILED, 0,
          "session_run", "Could not copy a requested output name.");
      goto cleanup;
    }
  }
  actual_input_count = 0u;
  for (index = 0u; index < input_count; ++index) {
    OrtValue* ort_value = dort_value_ort_for_run(inputs[index].value);
    dort_value_retain((dort_value_t*)inputs[index].value);
    ++retained_input_count;
    if (ort_value != NULL) {
      input_names[actual_input_count] = inputs[index].name_utf8;
      input_values[actual_input_count] = ort_value;
      ++actual_input_count;
    }
  }
  if (run_options != NULL) {
    dort_run_options_retain(run_options);
  }
  dort_session_retain(session);
  session_retained = 1;
  api = dort_runtime_api(runtime);
  dort_session_internal_lock(session);
  ort_status = api->Run(dort_session_internal_ort(session),
                        run_options == NULL ? NULL : run_options->options,
                        input_names, input_values, actual_input_count,
                        output_names_utf8, output_count, outputs);
  dort_session_internal_unlock(session);
  if (run_options != NULL) {
    dort_run_options_release(run_options);
  }
  for (index = 0u; index < retained_input_count; ++index) {
    dort_value_release((dort_value_t*)inputs[index].value);
  }
  retained_input_count = 0u;
  if (ort_status != NULL) {
    status = dort_status_from_ort(runtime, ort_status, DORT_ERROR_RUN_FAILED,
                                  "session_run");
    goto cleanup;
  }
  for (index = 0u; index < output_count; ++index) {
    size_t session_index = 0u;
    if (outputs[index] == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API, DORT_ERROR_RUN_FAILED, 0, "session_run",
          "ONNX Runtime returned a null requested output.");
      goto cleanup;
    }
    (void)dort_find_name_index(output_names_utf8[index], session, 0,
                               &session_index);
    if (dort_session_internal_output_kind(session, session_index) ==
        DORT_VALUE_KIND_OPTIONAL) {
      status = dort_value_wrap_optional_output(runtime, outputs[index],
                                               &result->values[index]);
      outputs[index] = NULL;
    } else {
      status = dort_value_wrap_owned(runtime, outputs[index],
                                     &result->values[index]);
      if (status == NULL) {
        outputs[index] = NULL;
      }
    }
    if (status != NULL) {
      goto cleanup;
    }
    status = dort_value_validate_supported(result->values[index],
                                           "session_run_output");
    if (status != NULL) {
      goto cleanup;
    }
  }
  dort_session_release(session);
  session_retained = 0;
  free(input_names);
  free(input_values);
  free(outputs);
  *out_result = result;
  return NULL;

cleanup:
  if (session_retained) {
    dort_session_release(session);
  }
  for (index = 0u; index < retained_input_count; ++index) {
    dort_value_release((dort_value_t*)inputs[index].value);
  }
  for (index = 0u; index < output_count; ++index) {
    if (outputs != NULL && outputs[index] != NULL) {
      api = dort_runtime_api(runtime);
      api->ReleaseValue(outputs[index]);
    }
  }
  free(input_names);
  free(input_values);
  free(outputs);
  dort_run_result_destroy(result);
  return status;
}

void DORT_CALL dort_run_result_retain(dort_run_result_t* result) {
  unsigned int references = 0u;
  if (!dort_run_result_is_valid(result)) {
    return;
  }
  references =
      atomic_load_explicit(&result->reference_count, memory_order_relaxed);
  while (references != 0u && references != UINT32_MAX) {
    if (atomic_compare_exchange_weak_explicit(
            &result->reference_count, &references, references + 1u,
            memory_order_relaxed, memory_order_relaxed)) {
      return;
    }
  }
}

void DORT_CALL dort_run_result_release(dort_run_result_t* result) {
  unsigned int previous = 0u;
  if (!dort_run_result_is_valid(result)) {
    return;
  }
  previous = atomic_fetch_sub_explicit(&result->reference_count, 1u,
                                       memory_order_acq_rel);
  if (previous == 1u) {
    dort_run_result_destroy(result);
  }
}

size_t DORT_CALL dort_run_result_count(const dort_run_result_t* result) {
  return dort_run_result_is_valid(result) ? result->count : 0u;
}

dort_status_t* DORT_CALL dort_run_result_get(const dort_run_result_t* result,
                                             size_t index,
                                             dort_string_t* out_name,
                                             dort_value_t** out_value) {
  dort_status_t* status = NULL;
  if (out_name != NULL) {
    memset(out_name, 0, sizeof(*out_name));
    out_name->struct_size = (uint32_t)sizeof(*out_name);
  }
  if (out_value != NULL) {
    *out_value = NULL;
  }
  if (out_name == NULL || out_value == NULL) {
    return dort_status_create(DORT_ERROR_DOMAIN_SHIM,
                              DORT_ERROR_INVALID_ARGUMENT, 0, "run_result_get",
                              "The output name or value pointer is null.");
  }
  if (!dort_run_result_is_valid(result) || index >= result->count) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM, DORT_ERROR_INVALID_ARGUMENT, 0,
        "run_result_get",
        "The run result is invalid or the output index is outside its bounds.");
  }
  status = dort_string_copy(result->names[index], out_name);
  if (status != NULL) {
    return status;
  }
  dort_value_retain(result->values[index]);
  *out_value = result->values[index];
  return NULL;
}
