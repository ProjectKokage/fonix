#include "dort_internal.h"

#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <pthread.h>
#endif

#ifndef FONIX_SHIM_BUILD_ID
#define FONIX_SHIM_BUILD_ID "development"
#endif

#define DORT_RUNTIME_MAGIC 0x44525431u

typedef struct dort_runtime_identity {
  struct dort_runtime_identity* next;
  dort_loaded_library_t library;
  const OrtApiBase* api_base;
  const OrtApi* api;
  char* runtime_version;
  char* log_id;
  uint32_t log_severity;
  uint32_t negotiated_api_version;
  OrtEnv* environment;
  size_t active_runtime_handles;
} dort_runtime_identity_t;

struct dort_runtime {
  uint32_t magic;
  atomic_uint reference_count;
  dort_runtime_identity_t* identity;
  uint32_t source_kind;
};

static dort_runtime_identity_t* dort_runtime_registry = NULL;

static dort_status_t* dort_status_from_api(
    const OrtApi* api,
    OrtStatus* ort_status,
    int32_t wrapper_code,
    const char* operation) {
  int32_t ort_code = 0;
  const char* message = "ONNX Runtime returned an unspecified error.";
  dort_status_t* status = NULL;
  if (ort_status == NULL) {
    return NULL;
  }
  if (api != NULL && api->GetErrorCode != NULL) {
    ort_code = (int32_t)api->GetErrorCode(ort_status);
  }
  if (api != NULL && api->GetErrorMessage != NULL) {
    const char* candidate = api->GetErrorMessage(ort_status);
    if (candidate != NULL) {
      message = candidate;
    }
  }
  status = dort_status_create(
      DORT_ERROR_DOMAIN_ORT_STATUS,
      wrapper_code,
      ort_code,
      operation,
      message);
  if (api != NULL && api->ReleaseStatus != NULL) {
    api->ReleaseStatus(ort_status);
  }
  return status;
}

static const char* dort_redacted_library_identity(const char* canonical_identity) {
  const char* slash = NULL;
  const char* backslash = NULL;
  if (canonical_identity == NULL) {
    return "unknown";
  }
  slash = strrchr(canonical_identity, '/');
  backslash = strrchr(canonical_identity, '\\');
  if (slash == NULL || (backslash != NULL && backslash > slash)) {
    slash = backslash;
  }
  return slash == NULL || slash[1] == '\0' ? canonical_identity : slash + 1;
}

#if defined(_WIN32)
static SRWLOCK dort_runtime_registry_lock = SRWLOCK_INIT;
static void dort_registry_lock(void) {
  AcquireSRWLockExclusive(&dort_runtime_registry_lock);
}
static void dort_registry_unlock(void) {
  ReleaseSRWLockExclusive(&dort_runtime_registry_lock);
}
#else
static pthread_mutex_t dort_runtime_registry_lock = PTHREAD_MUTEX_INITIALIZER;
static void dort_registry_lock(void) {
  (void)pthread_mutex_lock(&dort_runtime_registry_lock);
}
static void dort_registry_unlock(void) {
  (void)pthread_mutex_unlock(&dort_runtime_registry_lock);
}
#endif

static dort_status_t* dort_validate_optional_utf8(
    const char* value,
    size_t maximum,
    const char* field_name) {
  size_t ignored = 0u;
  int validation = DORT_ERROR_NONE;
  if (value == NULL) {
    return NULL;
  }
  validation = dort_bounded_utf8_length(value, maximum, 0, &ignored);
  if (validation == DORT_ERROR_NONE) {
    return NULL;
  }
  return dort_status_createf(
      DORT_ERROR_DOMAIN_SHIM,
      validation,
      0,
      "runtime_open",
      "%s must be non-empty valid UTF-8 within the ABI limit.",
      field_name);
}

static dort_status_t* dort_validate_runtime_config(
    const dort_runtime_config_t* config) {
  size_t log_id_length = 0u;
  size_t index = 0u;
  int validation = DORT_ERROR_NONE;
  dort_status_t* status = NULL;

  if (config == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "runtime_open",
        "The runtime configuration is null.");
  }
  if (config->struct_size < DORT_RUNTIME_CONFIG_V1_SIZE) {
    return dort_status_createf(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "runtime_open",
        "Runtime configuration size %u is smaller than ABI-v1 size %u.",
        config->struct_size,
        DORT_RUNTIME_CONFIG_V1_SIZE);
  }
  if (config->shim_abi_version != DORT_ABI_VERSION) {
    return dort_status_createf(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_ABI_MISMATCH,
        0,
        "runtime_open",
        "Shim ABI %u was requested, but this library implements ABI %u.",
        config->shim_abi_version,
        DORT_ABI_VERSION);
  }
  if (config->required_ort_api_version !=
      DORT_ORT_API_COMPATIBILITY_FLOOR) {
    return dort_status_createf(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_API_REQUEST_UNSUPPORTED,
        0,
        "runtime_open",
        "Shim ABI 1 supports exactly ONNX Runtime C API %u; API %u was requested.",
        DORT_ORT_API_COMPATIBILITY_FLOOR,
        config->required_ort_api_version);
  }
  if (!dort_runtime_profile_supports(config->source_kind)) {
    return dort_status_createf(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_SOURCE_UNSUPPORTED,
        0,
        "runtime_open",
        "Runtime source '%s' is not supported by the '%s' native build profile.",
        dort_runtime_source_name(config->source_kind),
        dort_runtime_profile_name());
  }
  if (config->flags != 0u || config->reserved0 != 0u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "runtime_open",
        "Runtime ABI-v1 flags and reserved fields must be zero.");
  }
  if (config->log_severity > DORT_LOG_FATAL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "runtime_open",
        "The runtime log severity is outside the ABI-v1 enum domain.");
  }
  validation = dort_bounded_utf8_length(
      config->log_id_utf8, DORT_MAX_LOG_ID_BYTES, 0, &log_id_length);
  if (validation != DORT_ERROR_NONE) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        validation,
        0,
        "runtime_open",
        "The runtime log ID must be non-empty valid UTF-8 within 128 bytes.");
  }
  (void)log_id_length;

  if (config->preferred_library_name_count > DORT_MAX_LIBRARY_NAMES) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "runtime_open",
        "The preferred runtime library-name count exceeds the ABI limit.");
  }
  if ((config->preferred_library_name_count == 0u) !=
      (config->preferred_library_names_utf8 == NULL)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "runtime_open",
        "The preferred runtime library-name pointer/count is inconsistent.");
  }
  for (index = 0u; index < config->preferred_library_name_count; ++index) {
    const char* name = config->preferred_library_names_utf8[index];
    size_t name_length = 0u;
    validation = dort_bounded_utf8_length(
        name, DORT_MAX_LIBRARY_NAME_BYTES, 0, &name_length);
    if (validation != DORT_ERROR_NONE || strchr(name, '/') != NULL ||
        strchr(name, '\\') != NULL) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          validation == DORT_ERROR_NONE ? DORT_ERROR_INVALID_ARGUMENT
                                        : validation,
          0,
          "runtime_open",
          "A process runtime library name is invalid, too long, or contains a path separator.");
    }
    (void)name_length;
  }

  status = dort_validate_optional_utf8(
      config->library_path_utf8, DORT_MAX_PATH_BYTES, "The runtime path");
  if (status != NULL) {
    return status;
  }
  status = dort_validate_optional_utf8(
      config->allowed_root_utf8, DORT_MAX_PATH_BYTES, "The allowed root");
  if (status != NULL) {
    return status;
  }

  switch (config->source_kind) {
    case DORT_RUNTIME_SOURCE_LINKED:
      if (config->library_path_utf8 != NULL ||
          config->allowed_root_utf8 != NULL ||
          config->preferred_library_name_count != 0u) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "runtime_open",
            "Linked runtime configuration must not contain loader paths or names.");
      }
      break;
    case DORT_RUNTIME_SOURCE_FILE:
      if (config->library_path_utf8 == NULL ||
          config->preferred_library_name_count != 0u) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "runtime_open",
            "A file runtime source requires one path and no process names.");
      }
      break;
    case DORT_RUNTIME_SOURCE_BUNDLED:
      if (config->library_path_utf8 != NULL ||
          config->allowed_root_utf8 != NULL ||
          config->preferred_library_name_count != 0u) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "runtime_open",
            "Bundled runtime configuration must not contain loader paths or "
            "names; the pinned runtime is resolved from the shim-owned "
            "application bundle.");
      }
      break;
    case DORT_RUNTIME_SOURCE_PROCESS:
      if (config->library_path_utf8 != NULL ||
          config->allowed_root_utf8 != NULL) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_INVALID_ARGUMENT,
            0,
            "runtime_open",
            "Process runtime configuration must not contain a file path or allowed root.");
      }
      break;
    default:
      return dort_status_create(
          DORT_ERROR_DOMAIN_UNSUPPORTED,
          DORT_ERROR_SOURCE_UNSUPPORTED,
          0,
          "runtime_open",
          "The runtime source enum value is unknown.");
  }
  return NULL;
}

static dort_runtime_identity_t* dort_find_runtime_identity(
    const dort_loaded_library_t* library,
    const char* log_id,
    uint32_t log_severity) {
  dort_runtime_identity_t* current = dort_runtime_registry;
  while (current != NULL) {
    if (current->log_severity == log_severity &&
        strcmp(current->log_id, log_id) == 0 &&
        strcmp(
            current->library.canonical_identity_utf8,
            library->canonical_identity_utf8) == 0 &&
        current->library.symbol_address == library->symbol_address) {
      return current;
    }
    current = current->next;
  }
  return NULL;
}

static dort_status_t* dort_negotiate_runtime(
    dort_loaded_library_t* library,
    const dort_runtime_config_t* config,
    dort_runtime_identity_t** out_identity) {
  const OrtApiBase* api_base = NULL;
  const OrtApi* api = NULL;
  const char* version = NULL;
  size_t version_length = 0u;
  size_t log_id_length = 0u;
  int validation = DORT_ERROR_NONE;
  dort_runtime_identity_t* identity = NULL;

  *out_identity = NULL;
  if (library->get_api_base == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_LOADER,
        DORT_ERROR_SYMBOL_NOT_FOUND,
        0,
        "runtime_open",
        "The resolved OrtGetApiBase function is null.");
  }
  api_base = library->get_api_base();
  if (api_base == NULL || api_base->GetVersionString == NULL ||
      api_base->GetApi == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_ORT_API_UNAVAILABLE,
        0,
        "runtime_open",
        "OrtGetApiBase returned an incomplete API-base table.");
  }
  version = api_base->GetVersionString();
  validation = dort_bounded_utf8_length(
      version, DORT_MAX_RUNTIME_VERSION_BYTES, 0, &version_length);
  if (validation != DORT_ERROR_NONE) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        validation,
        0,
        "runtime_open",
        "ONNX Runtime returned an invalid or oversized version string.");
  }
  api = api_base->GetApi(config->required_ort_api_version);
  if (api == NULL) {
    return dort_status_createf(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_ORT_API_UNAVAILABLE,
        0,
        "runtime_open",
        "Resolved ONNX Runtime %s does not provide required C API %u.",
        version,
        config->required_ort_api_version);
  }

  identity = (dort_runtime_identity_t*)calloc(1u, sizeof(*identity));
  if (identity == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_open",
        "Could not allocate the runtime identity.");
  }
  identity->runtime_version = dort_copy_c_string(version, version_length);
  validation = dort_bounded_utf8_length(
      config->log_id_utf8, DORT_MAX_LOG_ID_BYTES, 0, &log_id_length);
  if (validation == DORT_ERROR_NONE) {
    identity->log_id = dort_copy_c_string(config->log_id_utf8, log_id_length);
  }
  if (identity->runtime_version == NULL || identity->log_id == NULL) {
    free(identity->runtime_version);
    free(identity->log_id);
    free(identity);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_open",
        "Could not copy runtime identity metadata.");
  }
  identity->library = *library;
  memset(library, 0, sizeof(*library));
  identity->api_base = api_base;
  identity->api = api;
  identity->log_severity = config->log_severity;
  identity->negotiated_api_version = config->required_ort_api_version;
  if (api->CreateEnv == NULL || api->ReleaseEnv == NULL ||
      api->GetErrorCode == NULL || api->GetErrorMessage == NULL ||
      api->ReleaseStatus == NULL) {
    dort_loader_discard(&identity->library);
    free(identity->runtime_version);
    free(identity->log_id);
    free(identity);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_ORT_API_UNAVAILABLE,
        0,
        "runtime_open",
        "ONNX Runtime returned an incomplete environment or status lifecycle table.");
  }
  {
    OrtStatus* ort_status = api->CreateEnv(
        (OrtLoggingLevel)config->log_severity,
        config->log_id_utf8,
        &identity->environment);
    if (ort_status != NULL || identity->environment == NULL) {
      dort_status_t* status = ort_status == NULL
                                  ? dort_status_create(
                                        DORT_ERROR_DOMAIN_ORT_API,
                                        DORT_ERROR_ORT_API_UNAVAILABLE,
                                        0,
                                        "runtime_open",
                                        "ONNX Runtime did not create an environment.")
                                  : dort_status_from_api(
                                        api,
                                        ort_status,
                                        DORT_ERROR_ORT_API_UNAVAILABLE,
                                        "runtime_open");
      if (identity->environment != NULL) {
        api->ReleaseEnv(identity->environment);
        identity->environment = NULL;
      }
      dort_loader_discard(&identity->library);
      free(identity->runtime_version);
      free(identity->log_id);
      free(identity);
      return status;
    }
  }
  *out_identity = identity;
  return NULL;
}

static dort_status_t* dort_ensure_runtime_environment(
    dort_runtime_identity_t* identity) {
  OrtStatus* ort_status = NULL;
  if (identity->environment != NULL) {
    return NULL;
  }
  ort_status = identity->api->CreateEnv(
      (OrtLoggingLevel)identity->log_severity,
      identity->log_id,
      &identity->environment);
  if (ort_status != NULL) {
    dort_status_t* status = dort_status_from_api(
        identity->api,
        ort_status,
        DORT_ERROR_ORT_API_UNAVAILABLE,
        "runtime_open");
    if (identity->environment != NULL) {
      identity->api->ReleaseEnv(identity->environment);
      identity->environment = NULL;
    }
    return status;
  }
  if (identity->environment == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_ORT_API_UNAVAILABLE,
        0,
        "runtime_open",
        "ONNX Runtime did not recreate its environment.");
  }
  return NULL;
}

static void dort_destroy_unregistered_identity(dort_runtime_identity_t* identity) {
  if (identity == NULL) {
    return;
  }
  if (identity->environment != NULL && identity->api != NULL &&
      identity->api->ReleaseEnv != NULL) {
    identity->api->ReleaseEnv(identity->environment);
  }
  dort_loader_discard(&identity->library);
  free(identity->runtime_version);
  free(identity->log_id);
  free(identity);
}

dort_status_t* DORT_CALL dort_runtime_open(
    const dort_runtime_config_t* config,
    dort_runtime_t** out_runtime) {
  dort_loaded_library_t library;
  dort_runtime_identity_t* candidate = NULL;
  dort_runtime_identity_t* identity = NULL;
  dort_runtime_t* runtime = NULL;
  dort_status_t* status = NULL;

  if (out_runtime == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "runtime_open",
        "The output runtime pointer is null.");
  }
  *out_runtime = NULL;
  status = dort_validate_runtime_config(config);
  if (status != NULL) {
    return status;
  }
  memset(&library, 0, sizeof(library));

  dort_registry_lock();
  status = dort_loader_open(config, &library);
  if (status == NULL) {
    identity = dort_find_runtime_identity(
        &library, config->log_id_utf8, config->log_severity);
    if (identity == NULL) {
      status = dort_negotiate_runtime(&library, config, &candidate);
      if (status == NULL) {
        identity = candidate;
      }
    } else {
      status = dort_ensure_runtime_environment(identity);
    }
  }
  if (identity != NULL && library.canonical_identity_utf8 != NULL) {
    dort_loader_discard(&library);
  }
  if (status == NULL) {
    runtime = (dort_runtime_t*)calloc(1u, sizeof(*runtime));
    if (runtime == NULL) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_ALLOCATION,
          DORT_ERROR_ALLOCATION_FAILED,
          0,
          "runtime_open",
          "Could not allocate the runtime handle.");
      if (candidate == NULL && identity != NULL &&
          identity->active_runtime_handles == 0u &&
          identity->environment != NULL) {
        identity->api->ReleaseEnv(identity->environment);
        identity->environment = NULL;
      }
    } else {
      if (candidate != NULL) {
        candidate->next = dort_runtime_registry;
        dort_runtime_registry = candidate;
      }
      ++identity->active_runtime_handles;
      runtime->magic = DORT_RUNTIME_MAGIC;
      atomic_init(&runtime->reference_count, 1u);
      runtime->identity = identity;
      runtime->source_kind = config->source_kind;
    }
  }
  dort_registry_unlock();
  if (status != NULL) {
    if (candidate != NULL && candidate != dort_runtime_registry) {
      dort_destroy_unregistered_identity(candidate);
    }
    dort_loader_discard(&library);
    return status;
  }
  *out_runtime = runtime;
  return NULL;
}

void DORT_CALL dort_runtime_retain(dort_runtime_t* runtime) {
  unsigned int references = 0u;
  if (runtime == NULL || runtime->magic != DORT_RUNTIME_MAGIC) {
    return;
  }
  references = atomic_load_explicit(
      &runtime->reference_count, memory_order_relaxed);
  while (references != 0u && references != UINT32_MAX) {
    if (atomic_compare_exchange_weak_explicit(
            &runtime->reference_count,
            &references,
            references + 1u,
            memory_order_relaxed,
            memory_order_relaxed)) {
      return;
    }
  }
}

void DORT_CALL dort_runtime_release(dort_runtime_t* runtime) {
  unsigned int previous = 0u;
  if (runtime == NULL || runtime->magic != DORT_RUNTIME_MAGIC) {
    return;
  }
  previous = atomic_fetch_sub_explicit(
      &runtime->reference_count, 1u, memory_order_acq_rel);
  if (previous == 1u) {
    dort_runtime_identity_t* identity = runtime->identity;
    runtime->magic = 0u;
    runtime->identity = NULL;
    dort_registry_lock();
    if (identity != NULL && identity->active_runtime_handles > 0u) {
      --identity->active_runtime_handles;
      if (identity->active_runtime_handles == 0u &&
          identity->environment != NULL) {
        identity->api->ReleaseEnv(identity->environment);
        identity->environment = NULL;
      }
    }
    dort_registry_unlock();
    free(runtime);
  }
}

int dort_runtime_is_valid(const dort_runtime_t* runtime) {
  return runtime != NULL && runtime->magic == DORT_RUNTIME_MAGIC &&
         runtime->identity != NULL && runtime->identity->api != NULL &&
         runtime->identity->environment != NULL;
}

const OrtApi* dort_runtime_api(const dort_runtime_t* runtime) {
  return dort_runtime_is_valid(runtime) ? runtime->identity->api : NULL;
}

OrtEnv* dort_runtime_env(const dort_runtime_t* runtime) {
  return dort_runtime_is_valid(runtime) ? runtime->identity->environment : NULL;
}

int dort_runtime_has_same_identity(
    const dort_runtime_t* left,
    const dort_runtime_t* right) {
  return dort_runtime_is_valid(left) && dort_runtime_is_valid(right) &&
         left->identity == right->identity;
}

dort_status_t* dort_status_from_ort(
    const dort_runtime_t* runtime,
    OrtStatus* ort_status,
    int32_t wrapper_code,
    const char* operation) {
  return dort_status_from_api(
      dort_runtime_api(runtime), ort_status, wrapper_code, operation);
}

dort_status_t* dort_runtime_require_api(
    const dort_runtime_t* runtime,
    uint32_t features,
    const char* operation) {
  const OrtApi* api = dort_runtime_api(runtime);
  int missing = 0;
  if (api == NULL) {
    missing = 1;
  } else if (api->GetErrorCode == NULL || api->GetErrorMessage == NULL ||
             api->ReleaseStatus == NULL) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_SESSION_OPTIONS) != 0u &&
             (api->CreateSessionOptions == NULL ||
              api->ReleaseSessionOptions == NULL ||
              api->SetSessionGraphOptimizationLevel == NULL ||
              api->SetSessionExecutionMode == NULL ||
              api->SetIntraOpNumThreads == NULL ||
              api->SetInterOpNumThreads == NULL ||
              api->EnableCpuMemArena == NULL ||
              api->DisableCpuMemArena == NULL ||
              api->EnableMemPattern == NULL || api->DisableMemPattern == NULL ||
              api->SetDeterministicCompute == NULL ||
              api->SetSessionLogId == NULL ||
              api->SetSessionLogSeverityLevel == NULL ||
              api->SetSessionLogVerbosityLevel == NULL ||
              api->EnableProfiling == NULL || api->DisableProfiling == NULL ||
              api->SetOptimizedModelFilePath == NULL ||
              api->AddSessionConfigEntry == NULL ||
              api->SessionOptionsAppendExecutionProvider == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_SESSION) != 0u &&
             (api->CreateSessionFromArray == NULL ||
              api->ReleaseSession == NULL ||
              api->SessionGetInputCount == NULL ||
              api->SessionGetOutputCount == NULL ||
              api->SessionGetInputName == NULL ||
              api->SessionGetOutputName == NULL ||
              api->SessionGetInputTypeInfo == NULL ||
              api->SessionGetOutputTypeInfo == NULL ||
              api->GetAllocatorWithDefaultOptions == NULL ||
              api->AllocatorFree == NULL ||
              api->GetOnnxTypeFromTypeInfo == NULL ||
              api->CastTypeInfoToTensorInfo == NULL ||
              api->GetTensorElementType == NULL ||
              api->TensorTypeAndShape_HasShape == NULL ||
              api->GetDimensionsCount == NULL || api->GetDimensions == NULL ||
              api->GetSymbolicDimensions == NULL ||
              api->ReleaseTypeInfo == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_RUN_PROFILING) != 0u &&
             (api->RunOptionsEnableProfiling == NULL ||
              api->RunOptionsDisableProfiling == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_TENSOR_CREATE) != 0u &&
             (api->CreateCpuMemoryInfo == NULL ||
              api->ReleaseMemoryInfo == NULL ||
              api->CreateTensorWithDataAsOrtValue == NULL ||
              api->ReleaseValue == NULL || api->GetValueType == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_TENSOR_READ) != 0u &&
             (api->GetTensorTypeAndShape == NULL ||
              api->ReleaseTensorTypeAndShapeInfo == NULL ||
              api->GetTensorElementType == NULL ||
              api->GetDimensionsCount == NULL || api->GetDimensions == NULL ||
              api->GetTensorSizeInBytes == NULL || api->GetTensorData == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_RUN) != 0u &&
             (api->CreateRunOptions == NULL || api->ReleaseRunOptions == NULL ||
              api->RunOptionsSetTerminate == NULL ||
              api->RunOptionsUnsetTerminate == NULL || api->Run == NULL ||
              api->ReleaseValue == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_STRING_TENSOR) != 0u &&
             (api->CreateTensorAsOrtValue == NULL ||
              api->GetAllocatorWithDefaultOptions == NULL ||
              api->GetResizedStringTensorElementBuffer == NULL ||
              api->GetStringTensorDataLength == NULL ||
              api->GetStringTensorContent == NULL ||
              api->GetStringTensorElementLength == NULL ||
              api->GetStringTensorElement == NULL ||
              api->GetTensorTypeAndShape == NULL ||
              api->GetTensorElementType == NULL ||
              api->GetDimensionsCount == NULL || api->GetDimensions == NULL ||
              api->ReleaseTensorTypeAndShapeInfo == NULL ||
              api->ReleaseValue == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_COMPOSITE_VALUE) != 0u &&
             (api->GetValueType == NULL || api->GetValueCount == NULL ||
              api->GetValue == NULL || api->CreateValue == NULL ||
              api->GetAllocatorWithDefaultOptions == NULL ||
              api->HasValue == NULL || api->ReleaseValue == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_RECURSIVE_METADATA) != 0u &&
             (api->GetOnnxTypeFromTypeInfo == NULL ||
              api->CastTypeInfoToTensorInfo == NULL ||
              api->GetTensorElementType == NULL ||
              api->TensorTypeAndShape_HasShape == NULL ||
              api->GetDimensionsCount == NULL || api->GetDimensions == NULL ||
              api->GetSymbolicDimensions == NULL ||
              api->CastTypeInfoToSequenceTypeInfo == NULL ||
              api->GetSequenceElementType == NULL ||
              api->CastTypeInfoToMapTypeInfo == NULL ||
              api->GetMapKeyType == NULL || api->GetMapValueType == NULL ||
              api->CastTypeInfoToOptionalTypeInfo == NULL ||
              api->GetOptionalContainedTypeInfo == NULL ||
              api->ReleaseTypeInfo == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_MODEL_METADATA) != 0u &&
             (api->SessionGetModelMetadata == NULL ||
              api->ModelMetadataGetProducerName == NULL ||
              api->ModelMetadataGetGraphName == NULL ||
              api->ModelMetadataGetDomain == NULL ||
              api->ModelMetadataGetDescription == NULL ||
              api->ModelMetadataGetGraphDescription == NULL ||
              api->ModelMetadataGetVersion == NULL ||
              api->ModelMetadataGetCustomMetadataMapKeys == NULL ||
              api->ModelMetadataLookupCustomMetadataMap == NULL ||
              api->ReleaseModelMetadata == NULL ||
              api->GetAllocatorWithDefaultOptions == NULL ||
              api->AllocatorFree == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_DATA_LEASE) != 0u &&
             (api->GetTensorMemoryInfo == NULL ||
              api->MemoryInfoGetDeviceType == NULL ||
              api->GetTensorData == NULL)) {
    missing = 1;
  } else if ((features & DORT_API_FEATURE_EXTERNAL_DATA) != 0u &&
             (api->CloneSessionOptions == NULL ||
              api->AddExternalInitializersFromFilesInMemory == NULL ||
              api->CreateSessionFromArray == NULL ||
              api->ReleaseSessionOptions == NULL ||
              api->ReleaseSession == NULL)) {
    missing = 1;
  }
  if (!missing) {
    return NULL;
  }
  return dort_status_create(
      DORT_ERROR_DOMAIN_ORT_API,
      DORT_ERROR_ORT_API_UNAVAILABLE,
      0,
      operation,
      "ONNX Runtime API 27 returned an incomplete function table for this operation.");
}

void* dort_runtime_find_optional_symbol(
    const dort_runtime_t* runtime,
    const char* symbol_name) {
  if (!dort_runtime_is_valid(runtime)) {
    return NULL;
  }
  return dort_loader_find_optional_symbol(
      &runtime->identity->library, symbol_name);
}

dort_status_t* DORT_CALL dort_runtime_info_json(
    const dort_runtime_t* runtime,
    dort_string_t* out_json) {
  const dort_runtime_identity_t* identity = NULL;
  char* escaped_version = NULL;
  char* escaped_identity = NULL;
  char* escaped_log_id = NULL;
  int required = 0;
  char* json = NULL;
  dort_status_t* status = NULL;

  if (out_json == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "runtime_info",
        "The output runtime-info string pointer is null.");
  }
  memset(out_json, 0, sizeof(*out_json));
  out_json->struct_size = (uint32_t)sizeof(*out_json);
  if (runtime == NULL || runtime->magic != DORT_RUNTIME_MAGIC ||
      runtime->identity == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "runtime_info",
        "The runtime handle is null or invalid.");
  }
  identity = runtime->identity;
  escaped_version =
      dort_json_escape(identity->runtime_version, strlen(identity->runtime_version));
  {
    const char* redacted_identity = dort_redacted_library_identity(
        identity->library.canonical_identity_utf8);
    escaped_identity =
        dort_json_escape(redacted_identity, strlen(redacted_identity));
  }
  escaped_log_id = dort_json_escape(identity->log_id, strlen(identity->log_id));
  if (escaped_version == NULL || escaped_identity == NULL ||
      escaped_log_id == NULL) {
    free(escaped_version);
    free(escaped_identity);
    free(escaped_log_id);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_info",
        "Could not escape runtime diagnostics.");
  }

  required = snprintf(
      NULL,
      0,
      "{\"schemaVersion\":1,\"nativeIdentity\":\"fonix_shim\","
      "\"shimAbiVersion\":1,\"shimBuildId\":\"%s\","
      "\"requiredOrtApiVersion\":27,\"negotiatedOrtApiVersion\":%u,"
      "\"runtimeVersion\":\"%s\",\"runtimeSource\":\"%s\","
      "\"runtimeProfile\":\"%s\",\"runtimeLibraryIdentity\":\"%s\","
      "\"logSeverity\":\"%s\",\"logId\":\"%s\"}",
      FONIX_SHIM_BUILD_ID,
      identity->negotiated_api_version,
      escaped_version,
      dort_runtime_source_name(runtime->source_kind),
      dort_runtime_profile_name(),
      escaped_identity,
      dort_log_severity_name(identity->log_severity),
      escaped_log_id);
  if (required < 0 || (size_t)required > DORT_MAX_STATUS_MESSAGE_BYTES * 4u) {
    free(escaped_version);
    free(escaped_identity);
    free(escaped_log_id);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "runtime_info",
        "The runtime diagnostics exceed the ABI limit.");
  }
  json = (char*)malloc((size_t)required + 1u);
  if (json == NULL) {
    free(escaped_version);
    free(escaped_identity);
    free(escaped_log_id);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "runtime_info",
        "Could not allocate runtime diagnostics.");
  }
  (void)snprintf(
      json,
      (size_t)required + 1u,
      "{\"schemaVersion\":1,\"nativeIdentity\":\"fonix_shim\","
      "\"shimAbiVersion\":1,\"shimBuildId\":\"%s\","
      "\"requiredOrtApiVersion\":27,\"negotiatedOrtApiVersion\":%u,"
      "\"runtimeVersion\":\"%s\",\"runtimeSource\":\"%s\","
      "\"runtimeProfile\":\"%s\",\"runtimeLibraryIdentity\":\"%s\","
      "\"logSeverity\":\"%s\",\"logId\":\"%s\"}",
      FONIX_SHIM_BUILD_ID,
      identity->negotiated_api_version,
      escaped_version,
      dort_runtime_source_name(runtime->source_kind),
      dort_runtime_profile_name(),
      escaped_identity,
      dort_log_severity_name(identity->log_severity),
      escaped_log_id);
  free(escaped_version);
  free(escaped_identity);
  free(escaped_log_id);

  status = dort_string_copy(json, out_json);
  free(json);
  return status;
}
