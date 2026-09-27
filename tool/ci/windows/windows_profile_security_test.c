#include "../../../src/dort_internal.h"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <aclapi.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <winioctl.h>

dort_status_t* dort_windows_test_profile_prepare(const char* root,
                                                 wchar_t** out_directory,
                                                 wchar_t** out_prefix,
                                                 HANDLE* out_root_handle,
                                                 HANDLE* out_directory_handle);
dort_status_t* dort_windows_test_profile_read_and_remove(
    const wchar_t* directory, HANDLE* root_handle, HANDLE* directory_handle,
    dort_string_t* out_profile_json);
int dort_windows_test_profile_remove_directory(const wchar_t* directory,
                                               HANDLE* directory_handle,
                                               HANDLE* root_handle);

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

typedef struct fonix_profile_fixture {
  wchar_t* directory;
  wchar_t* prefix;
  HANDLE root_handle;
  HANDLE directory_handle;
} fonix_profile_fixture_t;

static void fonix_cleanup_fixture(fonix_profile_fixture_t* fixture);

_Static_assert(offsetof(fonix_mount_point_reparse_data_t, path_buffer) == 16u,
               "unexpected Windows mount-point reparse layout");

static int fonix_join_path(const wchar_t* parent, const wchar_t* leaf,
                           wchar_t* output, size_t output_count) {
  int written = swprintf(output, output_count, L"%ls\\%ls", parent, leaf);
  return written > 0 && (size_t)written < output_count;
}

static int fonix_profile_path(const wchar_t* prefix, const wchar_t* suffix,
                              wchar_t* output, size_t output_count) {
  int written = swprintf(output, output_count, L"%ls%ls", prefix, suffix);
  return written > 0 && (size_t)written < output_count;
}

static char* fonix_wide_to_utf8(const wchar_t* value) {
  int required = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1,
                                     NULL, 0, NULL, NULL);
  char* result = NULL;
  if (required <= 0) {
    return NULL;
  }
  result = (char*)malloc((size_t)required);
  if (result == NULL ||
      WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, result,
                          required, NULL, NULL) != required) {
    free(result);
    return NULL;
  }
  return result;
}

static int fonix_path_exists(const wchar_t* path) {
  return GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES;
}

static int fonix_has_protected_dacl(HANDLE handle) {
  BYTE system_sid[SECURITY_MAX_SID_SIZE];
  BYTE owner_rights_sid[SECURITY_MAX_SID_SIZE];
  DWORD system_sid_size = (DWORD)sizeof(system_sid);
  DWORD owner_rights_sid_size = (DWORD)sizeof(owner_rights_sid);
  PSECURITY_DESCRIPTOR descriptor = NULL;
  PACL dacl = NULL;
  ACL_SIZE_INFORMATION acl_information = {0};
  BOOL dacl_present = FALSE;
  BOOL dacl_defaulted = TRUE;
  SECURITY_DESCRIPTOR_CONTROL control = 0u;
  DWORD revision = 0u;
  DWORD index = 0u;
  int saw_system = 0;
  int saw_owner_rights = 0;
  DWORD result =
      GetSecurityInfo(handle, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, NULL,
                      NULL, &dacl, NULL, &descriptor);
  int success =
      result == ERROR_SUCCESS && descriptor != NULL &&
      GetSecurityDescriptorDacl(descriptor, &dacl_present, &dacl,
                                &dacl_defaulted) != 0 &&
      GetSecurityDescriptorControl(descriptor, &control, &revision) != 0 &&
      dacl_present != FALSE && dacl != NULL && dacl_defaulted == FALSE &&
      (control & SE_DACL_PROTECTED) != 0u &&
      GetAclInformation(dacl, &acl_information, (DWORD)sizeof(acl_information),
                        AclSizeInformation) != 0 &&
      acl_information.AceCount == 2u &&
      CreateWellKnownSid(WinLocalSystemSid, NULL, system_sid,
                         &system_sid_size) != 0 &&
      CreateWellKnownSid(WinCreatorOwnerRightsSid, NULL, owner_rights_sid,
                         &owner_rights_sid_size) != 0;
  for (index = 0u; success && index < acl_information.AceCount; ++index) {
    void* opaque_ace = NULL;
    ACCESS_ALLOWED_ACE* ace = NULL;
    if (!GetAce(dacl, index, &opaque_ace) || opaque_ace == NULL) {
      success = 0;
      break;
    }
    ace = (ACCESS_ALLOWED_ACE*)opaque_ace;
    if (ace->Header.AceType != ACCESS_ALLOWED_ACE_TYPE ||
        ace->Mask != FILE_ALL_ACCESS) {
      success = 0;
      break;
    }
    if (EqualSid(&ace->SidStart, system_sid)) {
      saw_system = 1;
    } else if (EqualSid(&ace->SidStart, owner_rights_sid)) {
      saw_owner_rights = 1;
    } else {
      success = 0;
    }
  }
  success = success && saw_system && saw_owner_rights;
  if (descriptor != NULL) {
    LocalFree(descriptor);
  }
  return success;
}

static int fonix_write_file(const wchar_t* path, const uint8_t* bytes,
                            DWORD length) {
  HANDLE file = CreateFileW(path, GENERIC_WRITE, 0u, NULL, CREATE_NEW,
                            FILE_ATTRIBUTE_NORMAL, NULL);
  DWORD written = 0u;
  int success = 0;
  if (file == INVALID_HANDLE_VALUE) {
    return 0;
  }
  if (length == 0u) {
    success = 1;
  } else {
    success = WriteFile(file, bytes, length, &written, NULL) != 0 &&
              written == length && FlushFileBuffers(file) != 0;
  }
  if (!CloseHandle(file)) {
    success = 0;
  }
  return success;
}

static int fonix_create_sized_file(const wchar_t* path, uint64_t length) {
  HANDLE file = CreateFileW(path, GENERIC_WRITE, 0u, NULL, CREATE_NEW,
                            FILE_ATTRIBUTE_NORMAL, NULL);
  LARGE_INTEGER offset;
  int success = 0;
  if (file == INVALID_HANDLE_VALUE || length > INT64_MAX) {
    if (file != INVALID_HANDLE_VALUE) {
      CloseHandle(file);
    }
    return 0;
  }
  offset.QuadPart = (LONGLONG)length;
  success = SetFilePointerEx(file, offset, NULL, FILE_BEGIN) != 0 &&
            SetEndOfFile(file) != 0 && FlushFileBuffers(file) != 0;
  if (!CloseHandle(file)) {
    success = 0;
  }
  return success;
}

static int fonix_create_junction(const wchar_t* junction_path,
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
          ? swprintf(substitute, sizeof(substitute) / sizeof(substitute[0]),
                     L"\\??\\UNC\\%ls", target_path + 2)
          : swprintf(substitute, sizeof(substitute) / sizeof(substitute[0]),
                     L"\\??\\%ls", target_path);
  int success = 0;
  if (written <= 0) {
    return 0;
  }
  substitute_chars = (size_t)written;
  substitute_bytes = substitute_chars * sizeof(wchar_t);
  print_bytes = print_chars * sizeof(wchar_t);
  data_bytes =
      8u + substitute_bytes + sizeof(wchar_t) + print_bytes + sizeof(wchar_t);
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
  memcpy((BYTE*)data->path_buffer + data->print_name_offset, target_path,
         print_bytes);
  directory = CreateFileW(
      junction_path, GENERIC_WRITE, 0u, NULL, OPEN_EXISTING,
      FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, NULL);
  if (directory != INVALID_HANDLE_VALUE) {
    success = DeviceIoControl(directory, FSCTL_SET_REPARSE_POINT, buffer,
                              (DWORD)(8u + data_bytes), NULL, 0u,
                              &bytes_returned, NULL) != 0;
    CloseHandle(directory);
  }
  if (!success) {
    RemoveDirectoryW(junction_path);
  }
  return success;
}

static void fonix_release_status(dort_status_t** status) {
  if (status != NULL && *status != NULL) {
    dort_status_release(*status);
    *status = NULL;
  }
}

static int fonix_prepare_fixture(const wchar_t* root,
                                 fonix_profile_fixture_t* fixture) {
  char* root_utf8 = fonix_wide_to_utf8(root);
  dort_status_t* status = NULL;
  memset(fixture, 0, sizeof(*fixture));
  if (root_utf8 == NULL) {
    return 0;
  }
  status = dort_windows_test_profile_prepare(
      root_utf8, &fixture->directory, &fixture->prefix, &fixture->root_handle,
      &fixture->directory_handle);
  free(root_utf8);
  if (status != NULL) {
    dort_status_release(status);
    return 0;
  }
  if (fixture->directory == NULL || fixture->prefix == NULL ||
      fixture->root_handle == NULL || fixture->directory_handle == NULL) {
    fonix_cleanup_fixture(fixture);
    return 0;
  }
  if (!fonix_has_protected_dacl(fixture->directory_handle)) {
    fonix_cleanup_fixture(fixture);
    return 0;
  }
  return 1;
}

static void fonix_cleanup_fixture(fonix_profile_fixture_t* fixture) {
  if (fixture == NULL) {
    return;
  }
  (void)dort_windows_test_profile_remove_directory(
      fixture->directory, &fixture->directory_handle, &fixture->root_handle);
  free(fixture->directory);
  free(fixture->prefix);
  memset(fixture, 0, sizeof(*fixture));
}

static int fonix_expect_finish_failure(fonix_profile_fixture_t* fixture,
                                       const char* label) {
  dort_string_t output;
  dort_status_t* status = NULL;
  int success = 1;
  memset(&output, 0, sizeof(output));
  output.struct_size = (uint32_t)sizeof(output);
  status = dort_windows_test_profile_read_and_remove(
      fixture->directory, &fixture->root_handle, &fixture->directory_handle,
      &output);
  if (status == NULL) {
    fprintf(stderr, "%s unexpectedly succeeded\n", label);
    success = 0;
  } else {
    dort_status_release(status);
  }
  if (output.data != NULL || output.length != 0u ||
      output.private_owner != NULL) {
    fprintf(stderr, "%s published bytes after rejection\n", label);
    dort_string_release(&output);
    success = 0;
  }
  if (fixture->root_handle != NULL || fixture->directory_handle != NULL ||
      fonix_path_exists(fixture->directory)) {
    fprintf(stderr, "%s did not retire its private directory\n", label);
    success = 0;
  }
  return success;
}

static int fonix_test_invalid_roots(const wchar_t* volume_root,
                                    const wchar_t* reparse_root,
                                    const wchar_t* reparse_child) {
  const wchar_t* roots[] = {volume_root, reparse_root, reparse_child};
  size_t index = 0u;
  wchar_t* directory = NULL;
  wchar_t* prefix = NULL;
  HANDLE root_handle = NULL;
  HANDLE directory_handle = NULL;
  dort_status_t* status = dort_windows_test_profile_prepare(
      "relative-profile-root", &directory, &prefix, &root_handle,
      &directory_handle);
  if (status == NULL || directory != NULL || prefix != NULL ||
      root_handle != NULL || directory_handle != NULL) {
    fprintf(stderr, "a relative profile root was accepted\n");
    fonix_release_status(&status);
    (void)dort_windows_test_profile_remove_directory(
        directory, &directory_handle, &root_handle);
    free(directory);
    free(prefix);
    return 0;
  }
  fonix_release_status(&status);
  for (index = 0u; index < sizeof(roots) / sizeof(roots[0]); ++index) {
    char* root_utf8 = fonix_wide_to_utf8(roots[index]);
    if (root_utf8 == NULL) {
      return 0;
    }
    status = dort_windows_test_profile_prepare(root_utf8, &directory, &prefix,
                                               &root_handle, &directory_handle);
    free(root_utf8);
    if (status == NULL || directory != NULL || prefix != NULL ||
        root_handle != NULL || directory_handle != NULL) {
      fprintf(stderr, "an unsafe profile root was accepted\n");
      fonix_release_status(&status);
      (void)dort_windows_test_profile_remove_directory(
          directory, &directory_handle, &root_handle);
      free(directory);
      free(prefix);
      return 0;
    }
    fonix_release_status(&status);
  }
  return 1;
}

static int fonix_test_unique_directories(const wchar_t* root) {
  fonix_profile_fixture_t first;
  fonix_profile_fixture_t second;
  int success = 0;
  memset(&first, 0, sizeof(first));
  memset(&second, 0, sizeof(second));
  if (!fonix_prepare_fixture(root, &first) ||
      !fonix_prepare_fixture(root, &second)) {
    fprintf(stderr, "could not prepare two simultaneous profile fixtures\n");
    goto cleanup;
  }
  if (wcscmp(first.directory, second.directory) == 0 ||
      wcscmp(first.prefix, second.prefix) == 0) {
    fprintf(stderr, "profile fixtures reused a private path\n");
    goto cleanup;
  }
  success = 1;

cleanup:
  fonix_cleanup_fixture(&second);
  fonix_cleanup_fixture(&first);
  return success;
}

static int fonix_test_lowercase_drive_root(const wchar_t* root) {
  fonix_profile_fixture_t fixture;
  wchar_t lowercase_root[DORT_MAX_PATH_BYTES];
  size_t length = wcslen(root);
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  if (length + 1u > sizeof(lowercase_root) / sizeof(lowercase_root[0])) {
    return 0;
  }
  if (length < 2u || root[1] != L':') {
    return 1;
  }
  memcpy(lowercase_root, root, (length + 1u) * sizeof(*lowercase_root));
  if (lowercase_root[0] >= L'A' && lowercase_root[0] <= L'Z') {
    lowercase_root[0] = (wchar_t)(lowercase_root[0] + (L'a' - L'A'));
  }
  if (!fonix_prepare_fixture(lowercase_root, &fixture)) {
    fprintf(stderr, "a lowercase drive-letter root was rejected\n");
    goto cleanup;
  }
  success = 1;

cleanup:
  fonix_cleanup_fixture(&fixture);
  return success;
}

static int fonix_test_success(const wchar_t* root) {
  static const uint8_t profile_bytes[] = {'[', ']'};
  fonix_profile_fixture_t fixture;
  wchar_t path[DORT_MAX_PATH_BYTES];
  dort_string_t output;
  dort_status_t* status = NULL;
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  memset(&output, 0, sizeof(output));
  output.struct_size = (uint32_t)sizeof(output);
  if (!fonix_prepare_fixture(root, &fixture) ||
      !fonix_profile_path(fixture.prefix, L"_123.json", path,
                          sizeof(path) / sizeof(path[0])) ||
      !fonix_write_file(path, profile_bytes, (DWORD)sizeof(profile_bytes))) {
    fprintf(stderr, "could not create the valid profile fixture\n");
    goto cleanup;
  }
  status = dort_windows_test_profile_read_and_remove(
      fixture.directory, &fixture.root_handle, &fixture.directory_handle,
      &output);
  if (status != NULL || output.length != sizeof(profile_bytes) ||
      output.data == NULL ||
      memcmp(output.data, profile_bytes, sizeof(profile_bytes)) != 0 ||
      fixture.root_handle != NULL || fixture.directory_handle != NULL ||
      fonix_path_exists(fixture.directory)) {
    fprintf(stderr, "the valid bounded profile was not consumed exactly\n");
    goto cleanup;
  }
  success = 1;

cleanup:
  fonix_release_status(&status);
  dort_string_release(&output);
  fonix_cleanup_fixture(&fixture);
  return success;
}

static int fonix_test_extra_file(const wchar_t* root) {
  static const uint8_t bytes[] = {'[', ']'};
  fonix_profile_fixture_t fixture;
  wchar_t expected[DORT_MAX_PATH_BYTES];
  wchar_t extra[DORT_MAX_PATH_BYTES];
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  if (!fonix_prepare_fixture(root, &fixture) ||
      !fonix_profile_path(fixture.prefix, L"_123.json", expected,
                          sizeof(expected) / sizeof(expected[0])) ||
      !fonix_join_path(fixture.directory, L"unexpected.tmp", extra,
                       sizeof(extra) / sizeof(extra[0])) ||
      !fonix_write_file(expected, bytes, (DWORD)sizeof(bytes)) ||
      !fonix_write_file(extra, bytes, (DWORD)sizeof(bytes))) {
    fprintf(stderr, "could not create the extra-file fixture\n");
    goto cleanup;
  }
  success = fonix_expect_finish_failure(&fixture, "extra profile file");

cleanup:
  fonix_cleanup_fixture(&fixture);
  return success;
}

static int fonix_test_expected_directory(const wchar_t* root) {
  fonix_profile_fixture_t fixture;
  wchar_t expected[DORT_MAX_PATH_BYTES];
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  if (!fonix_prepare_fixture(root, &fixture) ||
      !fonix_profile_path(fixture.prefix, L"_123.json", expected,
                          sizeof(expected) / sizeof(expected[0])) ||
      !CreateDirectoryW(expected, NULL)) {
    fprintf(stderr, "could not create the directory-entry fixture\n");
    goto cleanup;
  }
  success = fonix_expect_finish_failure(&fixture, "directory profile artifact");

cleanup:
  fonix_cleanup_fixture(&fixture);
  return success;
}

static int fonix_test_partial_name(const wchar_t* root) {
  static const uint8_t bytes[] = {'[', ']'};
  fonix_profile_fixture_t fixture;
  wchar_t partial[DORT_MAX_PATH_BYTES];
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  if (!fonix_prepare_fixture(root, &fixture) ||
      !fonix_profile_path(fixture.prefix, L"_123.json.partial", partial,
                          sizeof(partial) / sizeof(partial[0])) ||
      !fonix_write_file(partial, bytes, (DWORD)sizeof(bytes))) {
    fprintf(stderr, "could not create the partial-name fixture\n");
    goto cleanup;
  }
  success = fonix_expect_finish_failure(&fixture, "partial profile file");

cleanup:
  fonix_cleanup_fixture(&fixture);
  return success;
}

static int fonix_test_empty_file(const wchar_t* root) {
  fonix_profile_fixture_t fixture;
  wchar_t expected[DORT_MAX_PATH_BYTES];
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  if (!fonix_prepare_fixture(root, &fixture) ||
      !fonix_profile_path(fixture.prefix, L"_123.json", expected,
                          sizeof(expected) / sizeof(expected[0])) ||
      !fonix_write_file(expected, NULL, 0u)) {
    fprintf(stderr, "could not create the empty profile fixture\n");
    goto cleanup;
  }
  success = fonix_expect_finish_failure(&fixture, "empty profile file");

cleanup:
  fonix_cleanup_fixture(&fixture);
  return success;
}

static int fonix_test_oversize_file(const wchar_t* root) {
  fonix_profile_fixture_t fixture;
  wchar_t expected[DORT_MAX_PATH_BYTES];
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  if (!fonix_prepare_fixture(root, &fixture) ||
      !fonix_profile_path(fixture.prefix, L"_123.json", expected,
                          sizeof(expected) / sizeof(expected[0])) ||
      !fonix_create_sized_file(
          expected, (uint64_t)DORT_MAX_PROVIDER_PROFILE_BYTES + 1u)) {
    fprintf(stderr, "could not create the oversized profile fixture\n");
    goto cleanup;
  }
  success = fonix_expect_finish_failure(&fixture, "oversized profile file");

cleanup:
  fonix_cleanup_fixture(&fixture);
  return success;
}

static int fonix_test_hard_link(const wchar_t* root,
                                const wchar_t* outside_file) {
  static const uint8_t bytes[] = {'[', ']'};
  fonix_profile_fixture_t fixture;
  wchar_t expected[DORT_MAX_PATH_BYTES];
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  DeleteFileW(outside_file);
  if (!fonix_write_file(outside_file, bytes, (DWORD)sizeof(bytes)) ||
      !fonix_prepare_fixture(root, &fixture) ||
      !fonix_profile_path(fixture.prefix, L"_123.json", expected,
                          sizeof(expected) / sizeof(expected[0])) ||
      !CreateHardLinkW(expected, outside_file, NULL)) {
    fprintf(stderr, "could not create the hard-link profile fixture\n");
    goto cleanup;
  }
  success = fonix_expect_finish_failure(&fixture, "hard-link profile file");
  if (!fonix_path_exists(outside_file)) {
    fprintf(stderr, "hard-link cleanup removed the outside file\n");
    success = 0;
  }

cleanup:
  fonix_cleanup_fixture(&fixture);
  DeleteFileW(outside_file);
  return success;
}

static int fonix_test_junction(const wchar_t* root,
                               const wchar_t* outside_directory,
                               const wchar_t* outside_sentinel) {
  static const uint8_t bytes[] = {'o', 'u', 't'};
  fonix_profile_fixture_t fixture;
  wchar_t expected[DORT_MAX_PATH_BYTES];
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  if (!fonix_write_file(outside_sentinel, bytes, (DWORD)sizeof(bytes)) ||
      !fonix_prepare_fixture(root, &fixture) ||
      !fonix_profile_path(fixture.prefix, L"_123.json", expected,
                          sizeof(expected) / sizeof(expected[0])) ||
      !fonix_create_junction(expected, outside_directory)) {
    fprintf(stderr, "could not create the junction profile fixture\n");
    goto cleanup;
  }
  success = fonix_expect_finish_failure(&fixture, "junction profile entry");
  if (!fonix_path_exists(outside_sentinel)) {
    fprintf(stderr, "junction cleanup traversed outside its private root\n");
    success = 0;
  }

cleanup:
  fonix_cleanup_fixture(&fixture);
  DeleteFileW(outside_sentinel);
  return success;
}

static int fonix_test_repeated_cleanup(const wchar_t* root) {
  fonix_profile_fixture_t fixture;
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  if (!fonix_prepare_fixture(root, &fixture)) {
    fprintf(stderr, "could not create the repeated-cleanup fixture\n");
    return 0;
  }
  if (!dort_windows_test_profile_remove_directory(
          fixture.directory, &fixture.directory_handle, &fixture.root_handle) ||
      !dort_windows_test_profile_remove_directory(
          fixture.directory, &fixture.directory_handle, &fixture.root_handle) ||
      fonix_path_exists(fixture.directory)) {
    fprintf(stderr, "profile cleanup was not idempotent\n");
    goto cleanup;
  }
  success = 1;

cleanup:
  fonix_cleanup_fixture(&fixture);
  return success;
}

static int
fonix_test_cleanup_without_retained_directory_handle(const wchar_t* root) {
  fonix_profile_fixture_t fixture;
  int success = 0;
  memset(&fixture, 0, sizeof(fixture));
  if (!fonix_prepare_fixture(root, &fixture)) {
    fprintf(stderr, "could not create the partial-cleanup fixture\n");
    return 0;
  }
  if (!CloseHandle(fixture.directory_handle)) {
    fprintf(stderr, "could not drop the retained directory handle\n");
    goto cleanup;
  }
  fixture.directory_handle = NULL;
  if (!dort_windows_test_profile_remove_directory(
          fixture.directory, &fixture.directory_handle, &fixture.root_handle) ||
      fixture.root_handle != NULL || fonix_path_exists(fixture.directory)) {
    fprintf(stderr, "partial-initialization cleanup left its directory\n");
    goto cleanup;
  }
  success = 1;

cleanup:
  fonix_cleanup_fixture(&fixture);
  return success;
}

int main(void) {
  wchar_t temporary_parent[DORT_MAX_PATH_BYTES] = {0};
  wchar_t temporary_root[DORT_MAX_PATH_BYTES] = {0};
  wchar_t artifact_root[DORT_MAX_PATH_BYTES] = {0};
  wchar_t reparse_root[DORT_MAX_PATH_BYTES] = {0};
  wchar_t reparse_child[DORT_MAX_PATH_BYTES] = {0};
  wchar_t real_child[DORT_MAX_PATH_BYTES] = {0};
  wchar_t outside_directory[DORT_MAX_PATH_BYTES] = {0};
  wchar_t outside_sentinel[DORT_MAX_PATH_BYTES] = {0};
  wchar_t outside_hard_link[DORT_MAX_PATH_BYTES] = {0};
  wchar_t volume_root[DORT_MAX_PATH_BYTES] = {0};
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
  temporary_name =
      GetTempFileNameW(temporary_parent, L"fnp", 0u, temporary_root);
  if (temporary_name == 0u || !DeleteFileW(temporary_root) ||
      !CreateDirectoryW(temporary_root, NULL) ||
      !fonix_join_path(temporary_root, L"artifacts", artifact_root,
                       sizeof(artifact_root) / sizeof(artifact_root[0])) ||
      !fonix_join_path(temporary_root, L"artifacts-alias", reparse_root,
                       sizeof(reparse_root) / sizeof(reparse_root[0])) ||
      !fonix_join_path(reparse_root, L"child", reparse_child,
                       sizeof(reparse_child) / sizeof(reparse_child[0])) ||
      !fonix_join_path(artifact_root, L"child", real_child,
                       sizeof(real_child) / sizeof(real_child[0])) ||
      !fonix_join_path(temporary_root, L"outside", outside_directory,
                       sizeof(outside_directory) /
                           sizeof(outside_directory[0])) ||
      !fonix_join_path(outside_directory, L"sentinel.txt", outside_sentinel,
                       sizeof(outside_sentinel) /
                           sizeof(outside_sentinel[0])) ||
      !fonix_join_path(
          temporary_root, L"outside-profile.json", outside_hard_link,
          sizeof(outside_hard_link) / sizeof(outside_hard_link[0])) ||
      !CreateDirectoryW(artifact_root, NULL) ||
      !CreateDirectoryW(real_child, NULL) ||
      !CreateDirectoryW(outside_directory, NULL) ||
      !fonix_create_junction(reparse_root, artifact_root) ||
      !GetVolumePathNameW(
          temporary_root, volume_root,
          (DWORD)(sizeof(volume_root) / sizeof(volume_root[0])))) {
    fprintf(stderr, "could not create the Windows profiling fixture\n");
    goto cleanup;
  }

  if (!fonix_test_invalid_roots(volume_root, reparse_root, reparse_child) ||
      !fonix_test_lowercase_drive_root(artifact_root) ||
      !fonix_test_unique_directories(artifact_root) ||
      !fonix_test_success(artifact_root) ||
      !fonix_test_extra_file(artifact_root) ||
      !fonix_test_expected_directory(artifact_root) ||
      !fonix_test_partial_name(artifact_root) ||
      !fonix_test_empty_file(artifact_root) ||
      !fonix_test_oversize_file(artifact_root) ||
      !fonix_test_hard_link(artifact_root, outside_hard_link) ||
      !fonix_test_junction(artifact_root, outside_directory,
                           outside_sentinel) ||
      !fonix_test_repeated_cleanup(artifact_root) ||
      !fonix_test_cleanup_without_retained_directory_handle(artifact_root)) {
    goto cleanup;
  }

  result = 0;

cleanup:
  DeleteFileW(outside_hard_link);
  DeleteFileW(outside_sentinel);
  RemoveDirectoryW(reparse_root);
  RemoveDirectoryW(real_child);
  RemoveDirectoryW(outside_directory);
  RemoveDirectoryW(artifact_root);
  RemoveDirectoryW(temporary_root);
  return result;
}
