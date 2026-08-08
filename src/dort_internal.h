#ifndef FONIX_DORT_INTERNAL_H_
#define FONIX_DORT_INTERNAL_H_

#include "dort.h"

#include <onnxruntime_c_api.h>

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

void* dort_memory_allocate(size_t size);
void* dort_memory_allocate_zeroed(size_t count, size_t size);
void* dort_memory_reallocate(void* pointer, size_t size);
void* dort_memory_aligned_allocate(size_t size, size_t alignment);
void dort_memory_aligned_free(void* pointer);
#if !defined(_WIN32)
char* dort_memory_realpath(const char* path);
#endif

#define DORT_MAX_LOG_ID_BYTES 128u
#define DORT_MAX_PATH_BYTES 4096u
#define DORT_MAX_LIBRARY_NAME_BYTES 255u
#define DORT_MAX_LIBRARY_NAMES 16u
#define DORT_MAX_RUNTIME_VERSION_BYTES 256u
#define DORT_MAX_STATUS_MESSAGE_BYTES 4096u
#define DORT_MAX_MODEL_BYTES_DEFAULT (512u * 1024u * 1024u)
#define DORT_MAX_MODEL_BYTES_HARD (2u * 1024u * 1024u * 1024u)
#define DORT_MAX_TENSOR_BYTES (1024u * 1024u * 1024u)
#define DORT_MAX_TENSOR_ELEMENTS (1024u * 1024u * 1024u)
#define DORT_MAX_TENSOR_DIMENSION (1024u * 1024u * 1024u)
#define DORT_MAX_TENSOR_RANK 32u
#define DORT_MAX_IO_COUNT 256u
#define DORT_MAX_NAME_BYTES 1024u
#define DORT_MAX_KEY_BYTES 256u
#define DORT_MAX_VALUE_BYTES 4096u
#define DORT_MAX_CONFIG_ENTRIES 128u
#define DORT_MAX_PROVIDER_COUNT 16u
#define DORT_MAX_METADATA_JSON_BYTES (1024u * 1024u)
#define DORT_MAX_OWNED_STRING_BYTES DORT_MAX_METADATA_JSON_BYTES
#define DORT_MAX_STRING_ELEMENTS (1024u * 1024u)
#define DORT_MAX_STRING_ELEMENT_BYTES DORT_MAX_OWNED_STRING_BYTES
#define DORT_MAX_STRING_TENSOR_BYTES (64u * 1024u * 1024u)
#define DORT_MAX_COMPOSITE_CHILDREN 1024u
#define DORT_MAX_VALUE_NESTING_DEPTH 8u
#define DORT_MAX_VALUE_NODES 4096u
#define DORT_MAX_CUSTOM_METADATA_ENTRIES 256u
#define DORT_MAX_CUSTOM_METADATA_VALUE_BYTES (64u * 1024u)
#define DORT_MAX_EXTERNAL_DATA_FILES 256u
#define DORT_MAX_EXTERNAL_DATA_NAME_BYTES 1024u
#define DORT_MAX_PROVIDER_PROFILE_BYTES (8u * 1024u * 1024u)

#define DORT_API_FEATURE_SESSION_OPTIONS 0x01u
#define DORT_API_FEATURE_SESSION 0x02u
#define DORT_API_FEATURE_TENSOR_CREATE 0x04u
#define DORT_API_FEATURE_TENSOR_READ 0x08u
#define DORT_API_FEATURE_RUN 0x10u
#define DORT_API_FEATURE_STRING_TENSOR 0x20u
#define DORT_API_FEATURE_COMPOSITE_VALUE 0x40u
#define DORT_API_FEATURE_RECURSIVE_METADATA 0x80u
#define DORT_API_FEATURE_MODEL_METADATA 0x100u
#define DORT_API_FEATURE_DATA_LEASE 0x200u
#define DORT_API_FEATURE_EXTERNAL_DATA 0x400u
#define DORT_API_FEATURE_RUN_PROFILING 0x800u

typedef const OrtApiBase*(ORT_API_CALL* dort_ort_get_api_base_fn)(void);

typedef struct dort_loaded_library {
  void* handle;
  void* symbol_address;
  dort_ort_get_api_base_fn get_api_base;
  char* canonical_identity_utf8;
  int close_on_discard;
} dort_loaded_library_t;

struct dort_status {
  uint32_t domain;
  int32_t code;
  int32_t ort_code;
  char* operation;
  char* message;
  int is_static;
};

dort_status_t* dort_status_create(
    uint32_t domain,
    int32_t code,
    int32_t ort_code,
    const char* operation,
    const char* message);

dort_status_t* dort_status_createf(
    uint32_t domain,
    int32_t code,
    int32_t ort_code,
    const char* operation,
    const char* format,
    ...);

dort_status_t* dort_status_vcreatef(
    uint32_t domain,
    int32_t code,
    int32_t ort_code,
    const char* operation,
    const char* format,
    va_list arguments);

dort_status_t* dort_string_copy(const char* value, dort_string_t* out_string);
dort_status_t* dort_string_copy_bytes(
    const uint8_t* value,
    size_t length,
    int reject_embedded_nul,
    dort_string_t* out_string);

int dort_checked_add_size(size_t left, size_t right, size_t* out_value);
int dort_checked_multiply_size(size_t left, size_t right, size_t* out_value);
int dort_bounded_utf8_length(
    const char* value,
    size_t maximum,
    int allow_empty,
    size_t* out_length);
int dort_validate_utf8_bytes(
    const uint8_t* value,
    size_t length,
    int reject_embedded_nul);
char* dort_copy_c_string(const char* value, size_t length);
char* dort_json_escape(const char* value, size_t length);

const char* dort_runtime_profile_name(void);
int dort_runtime_profile_supports(uint32_t source_kind);
const char* dort_runtime_source_name(uint32_t source_kind);
const char* dort_log_severity_name(uint32_t severity);

dort_status_t* dort_loader_open(
    const dort_runtime_config_t* config,
    dort_loaded_library_t* out_library);
void dort_loader_discard(dort_loaded_library_t* library);
void* dort_loader_find_optional_symbol(
    const dort_loaded_library_t* library,
    const char* symbol_name);

int dort_runtime_is_valid(const dort_runtime_t* runtime);
const OrtApi* dort_runtime_api(const dort_runtime_t* runtime);
OrtEnv* dort_runtime_env(const dort_runtime_t* runtime);
int dort_runtime_has_same_identity(
    const dort_runtime_t* left,
    const dort_runtime_t* right);
dort_status_t* dort_status_from_ort(
    const dort_runtime_t* runtime,
    OrtStatus* ort_status,
    int32_t wrapper_code,
    const char* operation);
dort_status_t* dort_runtime_require_api(
    const dort_runtime_t* runtime,
    uint32_t features,
    const char* operation);
void* dort_runtime_find_optional_symbol(
    const dort_runtime_t* runtime,
    const char* symbol_name);

dort_runtime_t* dort_buffer_runtime(const dort_buffer_t* buffer);
void* dort_buffer_data(const dort_buffer_t* buffer);
dort_runtime_t* dort_value_runtime(const dort_value_t* value);
OrtValue* dort_value_ort(const dort_value_t* value);
dort_status_t* dort_value_wrap_owned(
    dort_runtime_t* runtime,
    OrtValue* ort_value,
    dort_value_t** out_value);
dort_status_t* dort_value_wrap_optional_output(
    dort_runtime_t* runtime,
    OrtValue* ort_value,
    dort_value_t** out_value);
dort_status_t* dort_value_validate_fixed_tensor(
    const dort_value_t* value,
    const char* operation);
dort_status_t* dort_value_validate_supported(
    const dort_value_t* value,
    const char* operation);
/* The caller must exclusively own value until this call returns. */
dort_status_t* dort_value_validate_and_set_unpublished_depth(
    dort_value_t* value,
    const char* operation);
OrtValue* dort_value_ort_for_run(const dort_value_t* value);
int dort_value_is_optional_none(const dort_value_t* value);

int dort_session_internal_is_valid(const dort_session_t* session);
dort_runtime_t* dort_session_internal_runtime(const dort_session_t* session);
OrtSession* dort_session_internal_ort(const dort_session_t* session);
size_t dort_session_internal_input_count(const dort_session_t* session);
size_t dort_session_internal_output_count(const dort_session_t* session);
const char* dort_session_internal_input_name(
    const dort_session_t* session,
    size_t index);
const char* dort_session_internal_output_name(
    const dort_session_t* session,
    size_t index);
uint32_t dort_session_internal_input_kind(
    const dort_session_t* session,
    size_t index);
uint32_t dort_session_internal_output_kind(
    const dort_session_t* session,
    size_t index);
void dort_session_internal_lock(dort_session_t* session);
void dort_session_internal_unlock(dort_session_t* session);

#endif /* FONIX_DORT_INTERNAL_H_ */
