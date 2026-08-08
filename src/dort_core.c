#include "dort_internal.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef FONIX_SHIM_BUILD_ID
#define FONIX_SHIM_BUILD_ID "development"
#endif

#ifndef FONIX_RUNTIME_PROFILE
#define FONIX_RUNTIME_PROFILE "external"
#endif

#if defined(FONIX_ANDROID_RUNTIME_OWNER_SHERPA) &&                         \
    defined(FONIX_ANDROID_RUNTIME_OWNER_APPLICATION)
#error "An Android Fonix shim must have exactly one runtime owner"
#elif defined(FONIX_ANDROID_RUNTIME_OWNER_SHERPA)
#if !defined(FONIX_RUNTIME_PROFILE_EXTERNAL)
#error "A sherpa-owned Android shim must use the external runtime profile"
#endif
#if defined(FONIX_ARTIFACT_ID)
#error "A sherpa-owned Android shim must not embed a second ORT artifact"
#endif
#define FONIX_ANDROID_RUNTIME_OWNER_JSON "\"sherpa\""
#define FONIX_ALLOWED_RUNTIME_SOURCES_JSON "[\"process\"]"
#elif defined(FONIX_ANDROID_RUNTIME_OWNER_APPLICATION)
#if !defined(FONIX_RUNTIME_PROFILE_BUNDLED)
#error "An application-owned Android shim must use the bundled profile"
#endif
#if !defined(FONIX_ARTIFACT_ID)
#error "An application-owned Android shim must embed its exact ORT artifact"
#endif
#define FONIX_ANDROID_RUNTIME_OWNER_JSON "\"application\""
#define FONIX_ALLOWED_RUNTIME_SOURCES_JSON "[\"bundled\"]"
#else
#define FONIX_ANDROID_RUNTIME_OWNER_JSON "null"
#if defined(FONIX_RUNTIME_PROFILE_EXTERNAL)
#define FONIX_ALLOWED_RUNTIME_SOURCES_JSON "[\"process\",\"file\"]"
#elif defined(FONIX_RUNTIME_PROFILE_BUNDLED)
#define FONIX_ALLOWED_RUNTIME_SOURCES_JSON "[\"bundled\"]"
#elif defined(FONIX_RUNTIME_PROFILE_LINKED)
#define FONIX_ALLOWED_RUNTIME_SOURCES_JSON "[\"linked\"]"
#else
#error "A FONIX_RUNTIME_PROFILE_* definition is required"
#endif
#endif

#if defined(FONIX_ARTIFACT_ID)
#if !defined(FONIX_ARTIFACT_LOCK_SHA256) ||                               \
    !defined(FONIX_ARTIFACT_SOURCE_SHA256) ||                             \
    !defined(FONIX_ARTIFACT_TARGET_OS) ||                                 \
    !defined(FONIX_ARTIFACT_TARGET_ARCHITECTURE) ||                       \
    !defined(FONIX_ARTIFACT_TARGET_VARIANT) ||                            \
    !defined(FONIX_ARTIFACT_MINIMUM_OS) ||                                \
    !defined(FONIX_ARTIFACT_FLAVOR) ||                                    \
    !defined(FONIX_ARTIFACT_RUNTIME_MODE) ||                              \
    !defined(FONIX_ARTIFACT_THIRD_PARTY_NOTICES_SHA256) ||                \
    !defined(FONIX_ARTIFACT_PROVIDERS_JSON)
#error "A selected Fonix artifact identity must define every closed field"
#endif
#define FONIX_ARTIFACT_MANIFEST_JSON                                      \
  "{\"id\":\"" FONIX_ARTIFACT_ID "\","                            \
  "\"lockSha256\":\"" FONIX_ARTIFACT_LOCK_SHA256 "\","            \
  "\"sourceSha256\":\"" FONIX_ARTIFACT_SOURCE_SHA256 "\","        \
  "\"targetOs\":\"" FONIX_ARTIFACT_TARGET_OS "\","                \
  "\"targetArchitecture\":\"" FONIX_ARTIFACT_TARGET_ARCHITECTURE    \
  "\",\"targetVariant\":\"" FONIX_ARTIFACT_TARGET_VARIANT "\","  \
  "\"minimumOs\":\"" FONIX_ARTIFACT_MINIMUM_OS "\","              \
  "\"flavor\":\"" FONIX_ARTIFACT_FLAVOR "\","                    \
  "\"runtimeMode\":\"" FONIX_ARTIFACT_RUNTIME_MODE "\","          \
  "\"thirdPartyNoticesSha256\":\""                                   \
  FONIX_ARTIFACT_THIRD_PARTY_NOTICES_SHA256 "\","                      \
  "\"providers\":" FONIX_ARTIFACT_PROVIDERS_JSON "}"
#else
#if defined(FONIX_ARTIFACT_LOCK_SHA256) ||                                \
    defined(FONIX_ARTIFACT_SOURCE_SHA256) ||                              \
    defined(FONIX_ARTIFACT_TARGET_OS) ||                                  \
    defined(FONIX_ARTIFACT_TARGET_ARCHITECTURE) ||                        \
    defined(FONIX_ARTIFACT_TARGET_VARIANT) ||                             \
    defined(FONIX_ARTIFACT_MINIMUM_OS) ||                                 \
    defined(FONIX_ARTIFACT_FLAVOR) ||                                     \
    defined(FONIX_ARTIFACT_RUNTIME_MODE) ||                               \
    defined(FONIX_ARTIFACT_THIRD_PARTY_NOTICES_SHA256) ||                 \
    defined(FONIX_ARTIFACT_PROVIDERS_JSON)
#error "A partial Fonix artifact identity is forbidden"
#endif
#define FONIX_ARTIFACT_MANIFEST_JSON "null"
#endif

uint32_t DORT_CALL dort_get_abi_version(void) {
  return DORT_ABI_VERSION;
}

uint32_t DORT_CALL dort_get_ort_api_compatibility_floor(void) {
  return DORT_ORT_API_COMPATIBILITY_FLOOR;
}

const char* dort_runtime_profile_name(void) {
  return FONIX_RUNTIME_PROFILE;
}

int dort_runtime_profile_supports(uint32_t source_kind) {
#if defined(FONIX_ANDROID_RUNTIME_OWNER_SHERPA)
  /*
   * The sherpa-owned Android composition is deliberately narrower than the
   * general desktop external profile.  Accepting FILE here would let an
   * application bypass the single process-owned ORT contract after the build
   * hook had correctly emitted a shim-only artifact.
   */
  return source_kind == DORT_RUNTIME_SOURCE_PROCESS;
#elif defined(FONIX_ANDROID_RUNTIME_OWNER_APPLICATION)
  return source_kind == DORT_RUNTIME_SOURCE_BUNDLED;
#elif defined(FONIX_RUNTIME_PROFILE_EXTERNAL)
  return source_kind == DORT_RUNTIME_SOURCE_PROCESS ||
         source_kind == DORT_RUNTIME_SOURCE_FILE;
#elif defined(FONIX_RUNTIME_PROFILE_BUNDLED)
  return source_kind == DORT_RUNTIME_SOURCE_BUNDLED;
#elif defined(FONIX_RUNTIME_PROFILE_LINKED)
  return source_kind == DORT_RUNTIME_SOURCE_LINKED;
#else
#error "A FONIX_RUNTIME_PROFILE_* definition is required"
#endif
}

const char* dort_runtime_source_name(uint32_t source_kind) {
  switch (source_kind) {
    case DORT_RUNTIME_SOURCE_LINKED:
      return "linked";
    case DORT_RUNTIME_SOURCE_BUNDLED:
      return "bundled";
    case DORT_RUNTIME_SOURCE_PROCESS:
      return "process";
    case DORT_RUNTIME_SOURCE_FILE:
      return "file";
    default:
      return "unknown";
  }
}

const char* dort_log_severity_name(uint32_t severity) {
  switch (severity) {
    case DORT_LOG_VERBOSE:
      return "verbose";
    case DORT_LOG_INFO:
      return "info";
    case DORT_LOG_WARNING:
      return "warning";
    case DORT_LOG_ERROR:
      return "error";
    case DORT_LOG_FATAL:
      return "fatal";
    default:
      return "unknown";
  }
}

int dort_checked_add_size(size_t left, size_t right, size_t* out_value) {
  if (out_value == NULL || left > SIZE_MAX - right) {
    return 0;
  }
  *out_value = left + right;
  return 1;
}

int dort_checked_multiply_size(size_t left, size_t right, size_t* out_value) {
  if (out_value == NULL || (left != 0u && right > SIZE_MAX / left)) {
    return 0;
  }
  *out_value = left * right;
  return 1;
}

char* dort_copy_c_string(const char* value, size_t length) {
  size_t allocation_size = 0;
  char* copy = NULL;

  if (value == NULL || !dort_checked_add_size(length, 1u, &allocation_size)) {
    return NULL;
  }
  copy = (char*)dort_memory_allocate(allocation_size);
  if (copy == NULL) {
    return NULL;
  }
  if (length > 0u) {
    memcpy(copy, value, length);
  }
  copy[length] = '\0';
  return copy;
}

static int dort_is_continuation(uint8_t value) {
  return (value & 0xc0u) == 0x80u;
}

int dort_validate_utf8_bytes(
    const uint8_t* value,
    size_t length,
    int reject_embedded_nul) {
  size_t index = 0u;
  if (length > 0u && value == NULL) {
    return DORT_ERROR_INVALID_ARGUMENT;
  }
  while (index < length) {
    const uint8_t first = value[index];
    if (first <= 0x7fu) {
      if (first == 0u && reject_embedded_nul) {
        return DORT_ERROR_INVALID_UTF8;
      }
      ++index;
      continue;
    }
    if (first >= 0xc2u && first <= 0xdfu) {
      if (index + 1u >= length || !dort_is_continuation(value[index + 1u])) {
        return DORT_ERROR_INVALID_UTF8;
      }
      index += 2u;
      continue;
    }
    if (first >= 0xe0u && first <= 0xefu) {
      if (index + 2u >= length || !dort_is_continuation(value[index + 1u]) ||
          !dort_is_continuation(value[index + 2u])) {
        return DORT_ERROR_INVALID_UTF8;
      }
      if ((first == 0xe0u && value[index + 1u] < 0xa0u) ||
          (first == 0xedu && value[index + 1u] >= 0xa0u)) {
        return DORT_ERROR_INVALID_UTF8;
      }
      index += 3u;
      continue;
    }
    if (first >= 0xf0u && first <= 0xf4u) {
      if (index + 3u >= length || !dort_is_continuation(value[index + 1u]) ||
          !dort_is_continuation(value[index + 2u]) ||
          !dort_is_continuation(value[index + 3u])) {
        return DORT_ERROR_INVALID_UTF8;
      }
      if ((first == 0xf0u && value[index + 1u] < 0x90u) ||
          (first == 0xf4u && value[index + 1u] >= 0x90u)) {
        return DORT_ERROR_INVALID_UTF8;
      }
      index += 4u;
      continue;
    }
    return DORT_ERROR_INVALID_UTF8;
  }
  return DORT_ERROR_NONE;
}

int dort_bounded_utf8_length(
    const char* value,
    size_t maximum,
    int allow_empty,
    size_t* out_length) {
  const uint8_t* bytes = (const uint8_t*)value;
  size_t length = 0;
  size_t index = 0;

  if (out_length != NULL) {
    *out_length = 0u;
  }
  if (value == NULL || out_length == NULL) {
    return DORT_ERROR_INVALID_ARGUMENT;
  }
  while (length <= maximum && value[length] != '\0') {
    ++length;
  }
  if (length > maximum) {
    return DORT_ERROR_LIMIT_EXCEEDED;
  }
  if (length == 0u && !allow_empty) {
    return DORT_ERROR_INVALID_ARGUMENT;
  }

  (void)bytes;
  (void)index;
  if (dort_validate_utf8_bytes((const uint8_t*)value, length, 1) !=
      DORT_ERROR_NONE) {
    return DORT_ERROR_INVALID_UTF8;
  }
  *out_length = length;
  return DORT_ERROR_NONE;
}

char* dort_json_escape(const char* value, size_t length) {
  size_t escaped_length = 0u;
  size_t index = 0u;
  char* result = NULL;
  size_t output = 0u;

  if (value == NULL) {
    return NULL;
  }
  for (index = 0u; index < length; ++index) {
    const unsigned char byte = (unsigned char)value[index];
    size_t increment = 1u;
    if (byte == '"' || byte == '\\' || byte == '\b' || byte == '\f' ||
        byte == '\n' || byte == '\r' || byte == '\t') {
      increment = 2u;
    } else if (byte < 0x20u) {
      increment = 6u;
    }
    if (!dort_checked_add_size(escaped_length, increment, &escaped_length)) {
      return NULL;
    }
  }
  if (!dort_checked_add_size(escaped_length, 1u, &escaped_length)) {
    return NULL;
  }
  result = (char*)dort_memory_allocate(escaped_length);
  if (result == NULL) {
    return NULL;
  }

  for (index = 0u; index < length; ++index) {
    const unsigned char byte = (unsigned char)value[index];
    switch (byte) {
      case '"':
        result[output++] = '\\';
        result[output++] = '"';
        break;
      case '\\':
        result[output++] = '\\';
        result[output++] = '\\';
        break;
      case '\b':
        result[output++] = '\\';
        result[output++] = 'b';
        break;
      case '\f':
        result[output++] = '\\';
        result[output++] = 'f';
        break;
      case '\n':
        result[output++] = '\\';
        result[output++] = 'n';
        break;
      case '\r':
        result[output++] = '\\';
        result[output++] = 'r';
        break;
      case '\t':
        result[output++] = '\\';
        result[output++] = 't';
        break;
      default:
        if (byte < 0x20u) {
          static const char hexadecimal[] = "0123456789abcdef";
          result[output++] = '\\';
          result[output++] = 'u';
          result[output++] = '0';
          result[output++] = '0';
          result[output++] = hexadecimal[(byte >> 4u) & 0x0fu];
          result[output++] = hexadecimal[byte & 0x0fu];
        } else {
          result[output++] = (char)byte;
        }
        break;
    }
  }
  result[output] = '\0';
  return result;
}

dort_status_t* dort_string_copy(const char* value, dort_string_t* out_string) {
  size_t length = 0u;
  char* copy = NULL;
  int validation = DORT_ERROR_NONE;

  if (out_string == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "string_copy",
        "The output string pointer is null.");
  }
  memset(out_string, 0, sizeof(*out_string));
  out_string->struct_size = (uint32_t)sizeof(*out_string);
  validation = dort_bounded_utf8_length(
      value, DORT_MAX_OWNED_STRING_BYTES, 1, &length);
  if (validation != DORT_ERROR_NONE) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        validation,
        0,
        "string_copy",
        "The native string is invalid or exceeds the ABI limit.");
  }
  copy = dort_copy_c_string(value, length);
  if (copy == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "string_copy",
        "Could not allocate the native string result.");
  }
  out_string->data = (const uint8_t*)copy;
  out_string->length = length;
  out_string->private_owner = copy;
  return NULL;
}

dort_status_t* dort_string_copy_bytes(
    const uint8_t* value,
    size_t length,
    int reject_embedded_nul,
    dort_string_t* out_string) {
  char* copy = NULL;
  int validation = DORT_ERROR_NONE;
  if (out_string == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "string_copy",
        "The output string pointer is null.");
  }
  memset(out_string, 0, sizeof(*out_string));
  out_string->struct_size = (uint32_t)sizeof(*out_string);
  if (length > DORT_MAX_OWNED_STRING_BYTES) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "string_copy",
        "The native string exceeds the owned-string ABI limit.");
  }
  validation = dort_validate_utf8_bytes(value, length, reject_embedded_nul);
  if (validation != DORT_ERROR_NONE) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        validation,
        0,
        "string_copy",
        "The native string bytes are not valid under the UTF-8 contract.");
  }
  copy = dort_copy_c_string((const char*)(value == NULL ? (const uint8_t*)"" : value), length);
  if (copy == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "string_copy",
        "Could not allocate the native string result.");
  }
  out_string->data = (const uint8_t*)copy;
  out_string->length = length;
  out_string->private_owner = copy;
  return NULL;
}

void DORT_CALL dort_string_release(dort_string_t* string_value) {
  if (string_value == NULL) {
    return;
  }
  free(string_value->private_owner);
  memset(string_value, 0, sizeof(*string_value));
}

dort_status_t* DORT_CALL dort_get_build_manifest_json(dort_string_t* out_json) {
  static const char manifest[] =
      "{\"schemaVersion\":3,"
      "\"nativeIdentity\":\"fonix_shim\","
      "\"shimAbiVersion\":1,"
      "\"requiredOrtApiVersion\":27,"
      "\"runtimeProfile\":\"" FONIX_RUNTIME_PROFILE "\","
      "\"androidRuntimeOwner\":" FONIX_ANDROID_RUNTIME_OWNER_JSON ","
      "\"allowedRuntimeSources\":" FONIX_ALLOWED_RUNTIME_SOURCES_JSON ","
      "\"buildId\":\"" FONIX_SHIM_BUILD_ID "\","
      "\"artifact\":" FONIX_ARTIFACT_MANIFEST_JSON "}";
  return dort_string_copy(manifest, out_json);
}
