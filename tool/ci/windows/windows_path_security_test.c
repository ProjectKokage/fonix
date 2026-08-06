#include "../../../src/dort_internal.h"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <winioctl.h>

dort_status_t* dort_windows_test_prepare_artifact_path(
    const char* path,
    const char* root,
    int reject_existing,
    char** out_path);
dort_status_t* dort_windows_test_read_model_file(
    const char* path,
    const char* allowed_root,
    size_t maximum,
    uint8_t** out_bytes,
    size_t* out_length);

typedef struct fonix_mount_point_reparse_data {
  ULONG tag;
  USHORT data_length;
  USHORT reserved;
  USHORT substitute_name_offset;
  USHORT substitute_name_length;
  USHORT print_name_offset;
  USHORT print_name_length;
  WCHAR path_buffer[1];
} fonix_mount_point_reparse_data_t;

_Static_assert(
    offsetof(fonix_mount_point_reparse_data_t, path_buffer) == 16u,
    "unexpected Windows mount-point reparse layout");

static int fonix_join_path(
    const wchar_t* parent,
    const wchar_t* leaf,
    wchar_t* output,
    size_t output_count) {
  int written = swprintf(output, output_count, L"%ls\\%ls", parent, leaf);
  return written > 0 && (size_t)written < output_count;
}

static char* fonix_wide_to_utf8(const wchar_t* value) {
  int required = WideCharToMultiByte(
      CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, NULL, 0, NULL, NULL);
  char* result = NULL;
  if (required <= 0) {
    return NULL;
  }
  result = (char*)malloc((size_t)required);
  if (result == NULL ||
      WideCharToMultiByte(
          CP_UTF8,
          WC_ERR_INVALID_CHARS,
          value,
          -1,
          result,
          required,
          NULL,
          NULL) != required) {
    free(result);
    return NULL;
  }
  return result;
}

static dort_status_t* fonix_prepare_artifact_path(
    const wchar_t* path,
    const wchar_t* root,
    int reject_existing,
    char** out_path) {
  char* path_utf8 = fonix_wide_to_utf8(path);
  char* root_utf8 = fonix_wide_to_utf8(root);
  dort_status_t* status = NULL;
  if (path_utf8 == NULL || root_utf8 == NULL) {
    free(path_utf8);
    free(root_utf8);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "windows_path_security_test",
        "Could not encode a test path.");
  }
  status = dort_windows_test_prepare_artifact_path(
      path_utf8, root_utf8, reject_existing, out_path);
  free(path_utf8);
  free(root_utf8);
  return status;
}

static dort_status_t* fonix_read_model_file(
    const wchar_t* path,
    const wchar_t* root,
    uint8_t** out_bytes,
    size_t* out_length) {
  char* path_utf8 = fonix_wide_to_utf8(path);
  char* root_utf8 = fonix_wide_to_utf8(root);
  dort_status_t* status = NULL;
  if (path_utf8 == NULL || root_utf8 == NULL) {
    free(path_utf8);
    free(root_utf8);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "windows_path_security_test",
        "Could not encode a test path.");
  }
  status = dort_windows_test_read_model_file(
      path_utf8, root_utf8, 1024u, out_bytes, out_length);
  free(path_utf8);
  free(root_utf8);
  return status;
}

static int fonix_write_file(
    const wchar_t* path,
    const uint8_t* bytes,
    DWORD length) {
  HANDLE file = CreateFileW(
      path,
      GENERIC_WRITE,
      0u,
      NULL,
      CREATE_NEW,
      FILE_ATTRIBUTE_NORMAL,
      NULL);
  DWORD written = 0u;
  int success = 0;
  if (file == INVALID_HANDLE_VALUE) {
    return 0;
  }
  success = WriteFile(file, bytes, length, &written, NULL) != 0 &&
            written == length && FlushFileBuffers(file) != 0;
  if (!CloseHandle(file)) {
    success = 0;
  }
  return success;
}

static int fonix_create_junction(
    const wchar_t* junction_path,
    const wchar_t* target_path) {
  BYTE buffer[MAXIMUM_REPARSE_DATA_BUFFER_SIZE];
  fonix_mount_point_reparse_data_t* data =
      (fonix_mount_point_reparse_data_t*)buffer;
  wchar_t substitute[DORT_MAX_PATH_BYTES];
  size_t substitute_chars = 0u;
  size_t print_chars = wcslen(target_path);
  size_t substitute_bytes = 0u;
  size_t print_bytes = 0u;
  size_t data_bytes = 0u;
  DWORD bytes_returned = 0u;
  HANDLE directory = INVALID_HANDLE_VALUE;
  int written =
      target_path[0] == L'\\' && target_path[1] == L'\\'
          ? swprintf(
                substitute,
                sizeof(substitute) / sizeof(substitute[0]),
                L"\\??\\UNC\\%ls",
                target_path + 2)
          : swprintf(
                substitute,
                sizeof(substitute) / sizeof(substitute[0]),
                L"\\??\\%ls",
                target_path);
  int success = 0;
  if (written <= 0) {
    return 0;
  }
  substitute_chars = (size_t)written;
  substitute_bytes = substitute_chars * sizeof(wchar_t);
  print_bytes = print_chars * sizeof(wchar_t);
  data_bytes = 8u + substitute_bytes + sizeof(wchar_t) + print_bytes +
               sizeof(wchar_t);
  if (data_bytes > UINT16_MAX ||
      offsetof(fonix_mount_point_reparse_data_t, path_buffer) +
              substitute_bytes + sizeof(wchar_t) + print_bytes +
              sizeof(wchar_t) >
          sizeof(buffer)) {
    return 0;
  }
  if (!CreateDirectoryW(junction_path, NULL)) {
    return 0;
  }
  memset(buffer, 0, sizeof(buffer));
  data->tag = IO_REPARSE_TAG_MOUNT_POINT;
  data->data_length = (USHORT)data_bytes;
  data->substitute_name_offset = 0u;
  data->substitute_name_length = (USHORT)substitute_bytes;
  data->print_name_offset = (USHORT)(substitute_bytes + sizeof(wchar_t));
  data->print_name_length = (USHORT)print_bytes;
  memcpy(data->path_buffer, substitute, substitute_bytes);
  memcpy(
      (BYTE*)data->path_buffer + data->print_name_offset,
      target_path,
      print_bytes);
  directory = CreateFileW(
      junction_path,
      GENERIC_WRITE,
      0u,
      NULL,
      OPEN_EXISTING,
      FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
      NULL);
  if (directory != INVALID_HANDLE_VALUE) {
    success = DeviceIoControl(
                  directory,
                  FSCTL_SET_REPARSE_POINT,
                  buffer,
                  (DWORD)(8u + data_bytes),
                  NULL,
                  0u,
                  &bytes_returned,
                  NULL) != 0;
    CloseHandle(directory);
  }
  if (!success) {
    RemoveDirectoryW(junction_path);
  }
  return success;
}

static int fonix_expect_failure(
    dort_status_t** status_pointer,
    const int32_t* accepted_codes,
    size_t accepted_code_count,
    const char* label) {
  size_t index = 0u;
  int32_t code = DORT_ERROR_NONE;
  int accepted = 0;
  dort_status_t* status = *status_pointer;
  if (status == NULL) {
    fprintf(stderr, "%s unexpectedly succeeded\n", label);
    return 0;
  }
  code = dort_status_code(status);
  for (index = 0u; index < accepted_code_count; ++index) {
    if (code == accepted_codes[index]) {
      accepted = 1;
      break;
    }
  }
  if (!accepted) {
    fprintf(stderr, "%s returned unexpected code %ld\n", label, (long)code);
  }
  dort_status_release(status);
  *status_pointer = NULL;
  return accepted;
}

int main(void) {
  static const uint8_t model_contents[] = {1u, 2u, 3u, 4u};
  static const int32_t outside_code[] = {
      DORT_ERROR_PATH_OUTSIDE_ALLOWED_ROOT};
  static const int32_t invalid_model_code[] = {DORT_ERROR_MODEL_INVALID};
  wchar_t temporary_parent[DORT_MAX_PATH_BYTES] = {0};
  wchar_t temporary_root[DORT_MAX_PATH_BYTES] = {0};
  wchar_t allowed_root[DORT_MAX_PATH_BYTES] = {0};
  wchar_t allowed_root_alias[DORT_MAX_PATH_BYTES] = {0};
  wchar_t outside_root[DORT_MAX_PATH_BYTES] = {0};
  wchar_t safe_directory[DORT_MAX_PATH_BYTES] = {0};
  wchar_t escape_junction[DORT_MAX_PATH_BYTES] = {0};
  wchar_t inside_junction[DORT_MAX_PATH_BYTES] = {0};
  wchar_t model_junction[DORT_MAX_PATH_BYTES] = {0};
  wchar_t valid_model[DORT_MAX_PATH_BYTES] = {0};
  wchar_t outside_model[DORT_MAX_PATH_BYTES] = {0};
  wchar_t escaped_model[DORT_MAX_PATH_BYTES] = {0};
  wchar_t inside_alias_model[DORT_MAX_PATH_BYTES] = {0};
  wchar_t valid_artifact[DORT_MAX_PATH_BYTES] = {0};
  wchar_t escaped_artifact[DORT_MAX_PATH_BYTES] = {0};
  wchar_t inside_alias_artifact[DORT_MAX_PATH_BYTES] = {0};
  char* prepared_path = NULL;
  uint8_t* model_bytes = NULL;
  size_t model_length = 0u;
  dort_status_t* status = NULL;
  UINT temporary_name = 0u;
  DWORD temporary_length = GetTempPathW(
      (DWORD)(sizeof(temporary_parent) / sizeof(temporary_parent[0])),
      temporary_parent);
  int result = 1;

  if (temporary_length == 0u ||
      temporary_length >=
          (DWORD)(sizeof(temporary_parent) / sizeof(temporary_parent[0]))) {
    fprintf(stderr, "could not locate the Windows temporary directory\n");
    return 1;
  }
  temporary_name = GetTempFileNameW(
      temporary_parent, L"fnx", 0u, temporary_root);
  if (temporary_name == 0u || !DeleteFileW(temporary_root) ||
      !CreateDirectoryW(temporary_root, NULL) ||
      !fonix_join_path(
          temporary_root,
          L"allowed",
          allowed_root,
          sizeof(allowed_root) / sizeof(allowed_root[0])) ||
      !fonix_join_path(
          temporary_root,
          L"allowed-alias",
          allowed_root_alias,
          sizeof(allowed_root_alias) / sizeof(allowed_root_alias[0])) ||
      !fonix_join_path(
          temporary_root,
          L"outside",
          outside_root,
          sizeof(outside_root) / sizeof(outside_root[0])) ||
      !fonix_join_path(
          allowed_root,
          L"safe",
          safe_directory,
          sizeof(safe_directory) / sizeof(safe_directory[0])) ||
      !CreateDirectoryW(allowed_root, NULL) ||
      !CreateDirectoryW(outside_root, NULL) ||
      !CreateDirectoryW(safe_directory, NULL)) {
    fprintf(stderr, "could not create the Windows path test fixture\n");
    goto cleanup;
  }
  if (!fonix_join_path(
          allowed_root,
          L"escape",
          escape_junction,
          sizeof(escape_junction) / sizeof(escape_junction[0])) ||
      !fonix_join_path(
          allowed_root,
          L"inside-alias",
          inside_junction,
          sizeof(inside_junction) / sizeof(inside_junction[0])) ||
      !fonix_join_path(
          allowed_root,
          L"model-link.onnx",
          model_junction,
          sizeof(model_junction) / sizeof(model_junction[0])) ||
      !fonix_create_junction(escape_junction, outside_root) ||
      !fonix_create_junction(inside_junction, safe_directory) ||
      !fonix_create_junction(model_junction, outside_root) ||
      !fonix_create_junction(allowed_root_alias, allowed_root)) {
    fprintf(stderr, "could not create the required unprivileged junction fixtures\n");
    goto cleanup;
  }
  if (!fonix_join_path(
          safe_directory,
          L"model.onnx",
          valid_model,
          sizeof(valid_model) / sizeof(valid_model[0])) ||
      !fonix_join_path(
          outside_root,
          L"outside.onnx",
          outside_model,
          sizeof(outside_model) / sizeof(outside_model[0])) ||
      !fonix_join_path(
          escape_junction,
          L"outside.onnx",
          escaped_model,
          sizeof(escaped_model) / sizeof(escaped_model[0])) ||
      !fonix_join_path(
          inside_junction,
          L"model.onnx",
          inside_alias_model,
          sizeof(inside_alias_model) / sizeof(inside_alias_model[0])) ||
      !fonix_write_file(
          valid_model, model_contents, (DWORD)sizeof(model_contents)) ||
      !fonix_write_file(
          outside_model, model_contents, (DWORD)sizeof(model_contents))) {
    fprintf(stderr, "could not create the bounded model fixtures\n");
    goto cleanup;
  }

  if (!fonix_join_path(
          safe_directory,
          L"profile-output",
          valid_artifact,
          sizeof(valid_artifact) / sizeof(valid_artifact[0])) ||
      !fonix_join_path(
          escape_junction,
          L"profile-output",
          escaped_artifact,
          sizeof(escaped_artifact) / sizeof(escaped_artifact[0])) ||
      !fonix_join_path(
          inside_junction,
          L"profile-output",
          inside_alias_artifact,
          sizeof(inside_alias_artifact) /
              sizeof(inside_alias_artifact[0]))) {
    fprintf(stderr, "could not compose the artifact fixture paths\n");
    goto cleanup;
  }

  status = fonix_prepare_artifact_path(
      valid_artifact, allowed_root, 1, &prepared_path);
  if (status != NULL || prepared_path == NULL) {
    fprintf(stderr, "a strict in-root artifact destination was rejected\n");
    dort_status_release(status);
    status = NULL;
    goto cleanup;
  }
  free(prepared_path);
  prepared_path = NULL;

  status = fonix_prepare_artifact_path(
      inside_alias_artifact, allowed_root, 1, &prepared_path);
  if (status != NULL || prepared_path == NULL) {
    fprintf(stderr, "an in-root junction artifact destination was rejected\n");
    dort_status_release(status);
    status = NULL;
    goto cleanup;
  }
  free(prepared_path);
  prepared_path = NULL;

  status = fonix_prepare_artifact_path(
      escaped_artifact, allowed_root, 1, &prepared_path);
  if (!fonix_expect_failure(
          &status,
          outside_code,
          sizeof(outside_code) / sizeof(outside_code[0]),
          "junction artifact escape") ||
      prepared_path != NULL) {
    goto cleanup;
  }

  status = fonix_prepare_artifact_path(
      escaped_model, allowed_root, 0, &prepared_path);
  if (!fonix_expect_failure(
          &status,
          outside_code,
          sizeof(outside_code) / sizeof(outside_code[0]),
          "existing junction artifact escape") ||
      prepared_path != NULL) {
    goto cleanup;
  }

  status = fonix_read_model_file(
      valid_model, allowed_root, &model_bytes, &model_length);
  if (status != NULL || model_length != sizeof(model_contents) ||
      model_bytes == NULL ||
      memcmp(model_bytes, model_contents, sizeof(model_contents)) != 0) {
    fprintf(stderr, "a strict non-reparse model child was rejected\n");
    dort_status_release(status);
    status = NULL;
    goto cleanup;
  }
  free(model_bytes);
  model_bytes = NULL;
  model_length = 0u;

  status = fonix_read_model_file(
      allowed_root, allowed_root, &model_bytes, &model_length);
  if (!fonix_expect_failure(
          &status,
          outside_code,
          sizeof(outside_code) / sizeof(outside_code[0]),
          "root-equality model path") ||
      model_bytes != NULL || model_length != 0u) {
    goto cleanup;
  }

  status = fonix_read_model_file(
      outside_model, allowed_root, &model_bytes, &model_length);
  if (!fonix_expect_failure(
          &status,
          outside_code,
          sizeof(outside_code) / sizeof(outside_code[0]),
          "non-child model path") ||
      model_bytes != NULL || model_length != 0u) {
    goto cleanup;
  }

  status = fonix_read_model_file(
      inside_alias_model, allowed_root, &model_bytes, &model_length);
  if (!fonix_expect_failure(
          &status,
          invalid_model_code,
          sizeof(invalid_model_code) / sizeof(invalid_model_code[0]),
          "reparse model parent") ||
      model_bytes != NULL || model_length != 0u) {
    goto cleanup;
  }

  status = fonix_read_model_file(
      model_junction, allowed_root, &model_bytes, &model_length);
  if (!fonix_expect_failure(
          &status,
          invalid_model_code,
          sizeof(invalid_model_code) / sizeof(invalid_model_code[0]),
          "reparse model leaf") ||
      model_bytes != NULL || model_length != 0u) {
    goto cleanup;
  }

  status = fonix_read_model_file(
      escaped_model, allowed_root, &model_bytes, &model_length);
  if (!fonix_expect_failure(
          &status,
          invalid_model_code,
          sizeof(invalid_model_code) / sizeof(invalid_model_code[0]),
          "escaping reparse model parent") ||
      model_bytes != NULL || model_length != 0u) {
    goto cleanup;
  }

  status = fonix_read_model_file(
      valid_model, allowed_root_alias, &model_bytes, &model_length);
  if (!fonix_expect_failure(
          &status,
          invalid_model_code,
          sizeof(invalid_model_code) / sizeof(invalid_model_code[0]),
          "reparse allowed root") ||
      model_bytes != NULL || model_length != 0u) {
    goto cleanup;
  }

  result = 0;

cleanup:
  dort_status_release(status);
  free(prepared_path);
  free(model_bytes);
  RemoveDirectoryW(allowed_root_alias);
  RemoveDirectoryW(model_junction);
  RemoveDirectoryW(inside_junction);
  RemoveDirectoryW(escape_junction);
  DeleteFileW(outside_model);
  DeleteFileW(valid_model);
  RemoveDirectoryW(safe_directory);
  RemoveDirectoryW(outside_root);
  RemoveDirectoryW(allowed_root);
  RemoveDirectoryW(temporary_root);
  return result;
}
