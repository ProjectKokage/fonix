#if !defined(_WIN32)
#define _POSIX_C_SOURCE 200112L
#endif

#include "dort_internal.h"

#include <inttypes.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#include <malloc.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <pthread.h>
#endif

#define DORT_BUFFER_MAGIC 0x44425531u
#define DORT_VALUE_MAGIC 0x44564131u
#define DORT_DATA_LEASE_MAGIC 0x444c5331u
#define DORT_LEASE_OWNER_BUFFER 1u
#define DORT_LEASE_OWNER_VALUE 2u

struct dort_buffer {
  uint32_t magic;
  atomic_uint reference_count;
  dort_runtime_t* runtime;
  void* data;
  size_t byte_length;
  size_t alignment;
  size_t attached_values;
  size_t active_mutable_leases;
#if defined(_WIN32)
  SRWLOCK lock;
#else
  pthread_mutex_t lock;
#endif
};

struct dort_value {
  uint32_t magic;
  atomic_uint reference_count;
  dort_runtime_t* runtime;
  OrtValue* value;
  dort_buffer_t* backing_buffer;
  dort_value_t* optional_contained;
  dort_value_t** retained_sources;
  size_t retained_source_count;
  uint32_t kind;
  uint32_t nesting_depth;
};

struct dort_data_lease {
  uint32_t magic;
  atomic_uint reference_count;
  uint32_t owner_kind;
  union {
    dort_buffer_t* buffer;
    dort_value_t* value;
  } owner;
};

static dort_status_t* dort_query_tensor_element_type(
    const dort_value_t* value,
    ONNXTensorElementDataType* out_element_type,
    const char* operation);
static dort_status_t* dort_query_fixed_tensor(
    const dort_value_t* value,
    ONNXTensorElementDataType* out_element_type,
    int64_t* out_dimensions,
    size_t* out_dimension_count,
    size_t* out_byte_length,
    const char* operation);
static dort_status_t* dort_query_string_tensor(
    const dort_value_t* value,
    size_t* out_string_count,
    size_t* out_total_bytes,
    int64_t* out_dimensions,
    size_t* out_dimension_count,
    const char* operation);

static int dort_is_power_of_two(size_t value) {
  return value != 0u && (value & (value - 1u)) == 0u;
}

static int dort_buffer_is_valid(const dort_buffer_t* buffer) {
  return buffer != NULL && buffer->magic == DORT_BUFFER_MAGIC &&
         dort_runtime_is_valid(buffer->runtime) && buffer->data != NULL;
}

static int dort_value_is_valid(const dort_value_t* value) {
  return value != NULL && value->magic == DORT_VALUE_MAGIC &&
         dort_runtime_is_valid(value->runtime) &&
         (value->value != NULL ||
          (value->kind == DORT_VALUE_KIND_OPTIONAL &&
           (value->optional_contained == NULL ||
            value->optional_contained->magic == DORT_VALUE_MAGIC)));
}

static int dort_data_lease_is_valid(const dort_data_lease_t* lease) {
  return lease != NULL && lease->magic == DORT_DATA_LEASE_MAGIC &&
         (lease->owner_kind == DORT_LEASE_OWNER_BUFFER ||
          lease->owner_kind == DORT_LEASE_OWNER_VALUE);
}

static void dort_aligned_free(void* pointer) {
#if defined(_WIN32)
  _aligned_free(pointer);
#else
  free(pointer);
#endif
}

static void dort_buffer_lock(dort_buffer_t* buffer) {
#if defined(_WIN32)
  AcquireSRWLockExclusive(&buffer->lock);
#else
  (void)pthread_mutex_lock(&buffer->lock);
#endif
}

static void dort_buffer_unlock(dort_buffer_t* buffer) {
#if defined(_WIN32)
  ReleaseSRWLockExclusive(&buffer->lock);
#else
  (void)pthread_mutex_unlock(&buffer->lock);
#endif
}

dort_status_t* DORT_CALL dort_buffer_allocate(
    dort_runtime_t* runtime,
    size_t byte_length,
    size_t alignment,
    dort_buffer_t** out_buffer) {
  dort_buffer_t* buffer = NULL;
  void* data = NULL;
  size_t allocation_length = byte_length == 0u ? 1u : byte_length;

  if (out_buffer == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "buffer_allocate",
        "The output buffer pointer is null.");
  }
  *out_buffer = NULL;
  if (!dort_runtime_is_valid(runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "buffer_allocate",
        "The runtime handle is null or invalid.");
  }
  if (byte_length > DORT_MAX_TENSOR_BYTES) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "buffer_allocate",
        "The requested native buffer exceeds the ABI byte limit.");
  }
  if (!dort_is_power_of_two(alignment) || alignment < sizeof(void*) ||
      alignment > 4096u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "buffer_allocate",
        "Alignment must be a power of two between pointer alignment and 4096 bytes.");
  }
#if defined(_WIN32)
  data = _aligned_malloc(allocation_length, alignment);
#else
  if (posix_memalign(&data, alignment, allocation_length) != 0) {
    data = NULL;
  }
#endif
  if (data == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "buffer_allocate",
        "Could not allocate the aligned native buffer.");
  }
  memset(data, 0, allocation_length);
  buffer = (dort_buffer_t*)calloc(1u, sizeof(*buffer));
  if (buffer == NULL) {
    dort_aligned_free(data);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "buffer_allocate",
        "Could not allocate the native buffer handle.");
  }
  dort_runtime_retain(runtime);
  buffer->magic = DORT_BUFFER_MAGIC;
  atomic_init(&buffer->reference_count, 1u);
  buffer->runtime = runtime;
  buffer->data = data;
  buffer->byte_length = byte_length;
  buffer->alignment = alignment;
#if defined(_WIN32)
  InitializeSRWLock(&buffer->lock);
#else
  if (pthread_mutex_init(&buffer->lock, NULL) != 0) {
    buffer->magic = 0u;
    dort_aligned_free(data);
    free(buffer);
    dort_runtime_release(runtime);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_PLATFORM,
        0,
        "buffer_allocate",
        "Could not initialize the native buffer lock.");
  }
#endif
  *out_buffer = buffer;
  return NULL;
}

void DORT_CALL dort_buffer_retain(dort_buffer_t* buffer) {
  unsigned int references = 0u;
  if (!dort_buffer_is_valid(buffer)) {
    return;
  }
  references = atomic_load_explicit(
      &buffer->reference_count, memory_order_relaxed);
  while (references != 0u && references != UINT32_MAX) {
    if (atomic_compare_exchange_weak_explicit(
            &buffer->reference_count,
            &references,
            references + 1u,
            memory_order_relaxed,
            memory_order_relaxed)) {
      return;
    }
  }
}

void DORT_CALL dort_buffer_release(dort_buffer_t* buffer) {
  unsigned int previous = 0u;
  if (!dort_buffer_is_valid(buffer)) {
    return;
  }
  previous = atomic_fetch_sub_explicit(
      &buffer->reference_count, 1u, memory_order_acq_rel);
  if (previous == 1u) {
    dort_runtime_t* runtime = buffer->runtime;
    void* data = buffer->data;
    buffer->magic = 0u;
    buffer->runtime = NULL;
    buffer->data = NULL;
#if !defined(_WIN32)
    (void)pthread_mutex_destroy(&buffer->lock);
#endif
    dort_aligned_free(data);
    free(buffer);
    dort_runtime_release(runtime);
  }
}

size_t DORT_CALL dort_buffer_byte_length(const dort_buffer_t* buffer) {
  return dort_buffer_is_valid(buffer) ? buffer->byte_length : 0u;
}

static dort_status_t* dort_validate_buffer_range(
    const dort_buffer_t* buffer,
    size_t offset,
    size_t byte_length,
    const void* pointer,
    const char* operation) {
  if (!dort_buffer_is_valid(buffer)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        operation,
        "The native buffer handle is null or invalid.");
  }
  if (byte_length > 0u && pointer == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        operation,
        "A non-empty buffer transfer requires a data pointer.");
  }
  if (offset > buffer->byte_length ||
      byte_length > buffer->byte_length - offset) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        operation,
        "The requested buffer range is outside the allocation.");
  }
  return NULL;
}

dort_status_t* DORT_CALL dort_buffer_write(
    dort_buffer_t* buffer,
    size_t offset,
    const void* source,
    size_t byte_length) {
  dort_status_t* status = dort_validate_buffer_range(
      buffer, offset, byte_length, source, "buffer_write");
  if (status == NULL) {
    dort_buffer_lock(buffer);
    if (buffer->attached_values != 0u || buffer->active_mutable_leases != 0u) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          buffer->attached_values != 0u ? DORT_ERROR_TENSOR_INVALID
                                        : DORT_ERROR_DATA_LEASE_UNSUPPORTED,
          0,
          "buffer_write",
          buffer->attached_values != 0u
              ? "A native buffer is immutable while attached to a tensor value."
              : "A native buffer transfer is blocked by its exclusive mutable data lease.");
    } else if (byte_length > 0u) {
      memcpy((uint8_t*)buffer->data + offset, source, byte_length);
    }
    dort_buffer_unlock(buffer);
  }
  return status;
}

dort_status_t* DORT_CALL dort_buffer_read(
    const dort_buffer_t* buffer,
    size_t offset,
    void* destination,
    size_t byte_length) {
  dort_status_t* status = dort_validate_buffer_range(
      buffer, offset, byte_length, destination, "buffer_read");
  if (status == NULL) {
    dort_buffer_lock((dort_buffer_t*)buffer);
    if (buffer->active_mutable_leases != 0u) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_DATA_LEASE_UNSUPPORTED,
          0,
          "buffer_read",
          "A native buffer transfer is blocked by its exclusive mutable data lease.");
    } else if (byte_length > 0u) {
      memcpy(destination, (const uint8_t*)buffer->data + offset, byte_length);
    }
    dort_buffer_unlock((dort_buffer_t*)buffer);
  }
  return status;
}

dort_status_t* DORT_CALL dort_buffer_data_acquire(
    dort_buffer_t* buffer,
    dort_data_lease_t** out_lease,
    void** out_data,
    size_t* out_byte_length) {
  dort_data_lease_t* lease = NULL;
  dort_status_t* status = NULL;
  if (out_lease != NULL) {
    *out_lease = NULL;
  }
  if (out_data != NULL) {
    *out_data = NULL;
  }
  if (out_byte_length != NULL) {
    *out_byte_length = 0u;
  }
  if (out_lease == NULL || out_data == NULL || out_byte_length == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "buffer_data_acquire",
        "All buffer data-acquisition output pointers are required.");
  }
  if (!dort_buffer_is_valid(buffer)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "buffer_data_acquire",
        "The native buffer handle is null or invalid.");
  }
  lease = (dort_data_lease_t*)calloc(1u, sizeof(*lease));
  if (lease == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "buffer_data_acquire",
        "Could not allocate the buffer data lease.");
  }
  dort_buffer_lock(buffer);
  if (buffer->attached_values != 0u || buffer->active_mutable_leases != 0u) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_DATA_LEASE_UNSUPPORTED,
        0,
        "buffer_data_acquire",
        "A mutable buffer lease requires an unattached buffer with no active lease.");
  } else {
    buffer->active_mutable_leases = 1u;
    dort_buffer_retain(buffer);
    lease->magic = DORT_DATA_LEASE_MAGIC;
    atomic_init(&lease->reference_count, 1u);
    lease->owner_kind = DORT_LEASE_OWNER_BUFFER;
    lease->owner.buffer = buffer;
    *out_lease = lease;
    *out_data = buffer->data;
    *out_byte_length = buffer->byte_length;
  }
  dort_buffer_unlock(buffer);
  if (status != NULL) {
    free(lease);
  }
  return status;
}

void DORT_CALL dort_data_lease_retain(dort_data_lease_t* lease) {
  unsigned int references = 0u;
  if (!dort_data_lease_is_valid(lease)) {
    return;
  }
  references = atomic_load_explicit(&lease->reference_count, memory_order_relaxed);
  while (references != 0u && references != UINT32_MAX) {
    if (atomic_compare_exchange_weak_explicit(
            &lease->reference_count,
            &references,
            references + 1u,
            memory_order_relaxed,
            memory_order_relaxed)) {
      return;
    }
  }
}

void DORT_CALL dort_data_lease_release(dort_data_lease_t* lease) {
  unsigned int previous = 0u;
  if (!dort_data_lease_is_valid(lease)) {
    return;
  }
  previous = atomic_fetch_sub_explicit(
      &lease->reference_count, 1u, memory_order_acq_rel);
  if (previous == 1u) {
    uint32_t owner_kind = lease->owner_kind;
    dort_buffer_t* buffer = owner_kind == DORT_LEASE_OWNER_BUFFER
                                ? lease->owner.buffer
                                : NULL;
    dort_value_t* value = owner_kind == DORT_LEASE_OWNER_VALUE
                              ? lease->owner.value
                              : NULL;
    lease->magic = 0u;
    lease->owner_kind = 0u;
    lease->owner.buffer = NULL;
    if (buffer != NULL) {
      dort_buffer_lock(buffer);
      if (buffer->active_mutable_leases > 0u) {
        --buffer->active_mutable_leases;
      }
      dort_buffer_unlock(buffer);
      dort_buffer_release(buffer);
    }
    if (value != NULL) {
      dort_value_release(value);
    }
    free(lease);
  }
}

dort_runtime_t* dort_buffer_runtime(const dort_buffer_t* buffer) {
  return dort_buffer_is_valid(buffer) ? buffer->runtime : NULL;
}

void* dort_buffer_data(const dort_buffer_t* buffer) {
  return dort_buffer_is_valid(buffer) ? buffer->data : NULL;
}

static size_t dort_element_width(uint32_t element_type) {
  switch (element_type) {
    case DORT_TENSOR_UINT8:
    case DORT_TENSOR_INT8:
    case DORT_TENSOR_BOOL:
      return 1u;
    case DORT_TENSOR_UINT16:
    case DORT_TENSOR_INT16:
    case DORT_TENSOR_FLOAT16:
    case DORT_TENSOR_BFLOAT16:
      return 2u;
    case DORT_TENSOR_FLOAT32:
    case DORT_TENSOR_INT32:
    case DORT_TENSOR_UINT32:
      return 4u;
    case DORT_TENSOR_INT64:
    case DORT_TENSOR_FLOAT64:
    case DORT_TENSOR_UINT64:
      return 8u;
    default:
      return 0u;
  }
}

static dort_status_t* dort_validate_tensor_shape(
    const int64_t* dimensions,
    size_t dimension_count,
    size_t* out_element_count,
    const char* operation) {
  size_t element_count = 1u;
  size_t index = 0u;
  *out_element_count = 0u;
  if (dimension_count > DORT_MAX_TENSOR_RANK) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        operation,
        "The dense tensor rank exceeds the ABI limit.");
  }
  if (dimension_count > 0u && dimensions == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        operation,
        "A non-scalar tensor requires a dimensions array.");
  }
  for (index = 0u; index < dimension_count; ++index) {
    size_t dimension = 0u;
    if (dimensions[index] < 0) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_TENSOR_INVALID,
          0,
          operation,
          "Concrete tensor dimensions must be non-negative.");
    }
    if ((uint64_t)dimensions[index] > (uint64_t)SIZE_MAX) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_OVERFLOW,
          0,
          operation,
          "A tensor dimension is not representable on this platform.");
    }
    dimension = (size_t)dimensions[index];
    if (dimension > DORT_MAX_TENSOR_DIMENSION) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_LIMIT_EXCEEDED,
          0,
          operation,
          "A tensor dimension exceeds the ABI limit.");
    }
    if (!dort_checked_multiply_size(element_count, dimension, &element_count)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_OVERFLOW,
          0,
          operation,
          "The tensor element count overflows the native size domain.");
    }
    if (element_count > DORT_MAX_TENSOR_ELEMENTS) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_LIMIT_EXCEEDED,
          0,
          operation,
          "The tensor element count exceeds the ABI limit.");
    }
  }
  *out_element_count = element_count;
  return NULL;
}

static dort_status_t* dort_validate_tensor_layout(
    size_t byte_length,
    const int64_t* dimensions,
    size_t dimension_count,
    uint32_t element_type) {
  size_t element_count = 0u;
  size_t expected_bytes = 0u;
  size_t width = dort_element_width(element_type);
  dort_status_t* status = dort_validate_tensor_shape(
      dimensions, dimension_count, &element_count, "tensor_create");
  if (status != NULL) {
    return status;
  }
  if (width == 0u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_TENSOR_INVALID,
        0,
        "tensor_create",
        "ABI v1 supports only dense fixed-width tensor element types.");
  }
  if (!dort_checked_multiply_size(element_count, width, &expected_bytes)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_OVERFLOW,
        0,
        "tensor_create",
        "The tensor byte count overflows the native size domain.");
  }
  if (expected_bytes > DORT_MAX_TENSOR_BYTES) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "tensor_create",
        "The dense tensor exceeds the ABI byte limit.");
  }
  if (expected_bytes != byte_length) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_TENSOR_INVALID,
        0,
        "tensor_create",
        "The supplied byte length does not match the dense tensor shape and element type.");
  }
  return NULL;
}

static dort_status_t* dort_buffer_attach_tensor(
    dort_buffer_t* buffer,
    size_t byte_offset,
    size_t byte_length,
    uint32_t element_type) {
  size_t index = 0u;
  dort_status_t* status = NULL;
  dort_buffer_lock(buffer);
  if (buffer->active_mutable_leases != 0u) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_DATA_LEASE_UNSUPPORTED,
        0,
        "tensor_create",
        "A tensor cannot attach while the native buffer has a mutable data lease.");
  } else if (element_type == DORT_TENSOR_BOOL) {
    const uint8_t* bytes = (const uint8_t*)buffer->data + byte_offset;
    for (index = 0u; index < byte_length; ++index) {
      if (bytes[index] > 1u) {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_SHIM,
            DORT_ERROR_TENSOR_INVALID,
            0,
            "tensor_create",
            "Boolean tensor bytes must use the closed zero-or-one domain.");
        break;
      }
    }
  }
  if (status == NULL) {
    ++buffer->attached_values;
  }
  dort_buffer_unlock(buffer);
  return status;
}

static void dort_buffer_detach_tensor(dort_buffer_t* buffer) {
  if (!dort_buffer_is_valid(buffer)) {
    return;
  }
  dort_buffer_lock(buffer);
  if (buffer->attached_values > 0u) {
    --buffer->attached_values;
  }
  dort_buffer_unlock(buffer);
}

static dort_status_t* dort_value_allocate(
    dort_runtime_t* runtime,
    OrtValue* ort_value,
    uint32_t kind,
    uint32_t nesting_depth,
    dort_value_t* optional_contained,
    dort_value_t** out_value) {
  dort_value_t* value = NULL;
  if (out_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_wrap",
        "The output value pointer is null.");
  }
  *out_value = NULL;
  if (!dort_runtime_is_valid(runtime) ||
      (ort_value == NULL && kind != DORT_VALUE_KIND_OPTIONAL) ||
      (kind != DORT_VALUE_KIND_TENSOR && kind != DORT_VALUE_KIND_SEQUENCE &&
       kind != DORT_VALUE_KIND_MAP && kind != DORT_VALUE_KIND_OPTIONAL) ||
      nesting_depth == 0u || nesting_depth > DORT_MAX_VALUE_NESTING_DEPTH) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_wrap",
        "The runtime, value kind, nesting depth, or ONNX Runtime value is invalid.");
  }
  value = (dort_value_t*)calloc(1u, sizeof(*value));
  if (value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "value_wrap",
        "Could not allocate the tensor value handle.");
  }
  dort_runtime_retain(runtime);
  value->magic = DORT_VALUE_MAGIC;
  atomic_init(&value->reference_count, 1u);
  value->runtime = runtime;
  value->value = ort_value;
  value->kind = kind;
  value->nesting_depth = nesting_depth;
  value->optional_contained = optional_contained;
  if (optional_contained != NULL) {
    dort_value_retain(optional_contained);
  }
  *out_value = value;
  return NULL;
}

static dort_status_t* dort_value_attach_sources(
    dort_value_t* value,
    const dort_value_t* const* sources,
    size_t source_count,
    const char* operation) {
  dort_value_t** retained = NULL;
  size_t index = 0u;
  if (!dort_value_is_valid(value) || sources == NULL || source_count == 0u ||
      source_count > DORT_MAX_COMPOSITE_CHILDREN || operation == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        operation == NULL ? "value_attach_sources" : operation,
        "Composite source ownership parameters are invalid.");
  }
  retained = (dort_value_t**)calloc(source_count, sizeof(*retained));
  if (retained == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        operation,
        "Could not allocate the bounded composite source-owner array.");
  }
  for (index = 0u; index < source_count; ++index) {
    if (!dort_value_is_valid(sources[index])) {
      while (index > 0u) {
        --index;
        dort_value_release(retained[index]);
      }
      free(retained);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          operation,
          "A composite source became invalid before ownership transfer.");
    }
    retained[index] = (dort_value_t*)sources[index];
    dort_value_retain(retained[index]);
  }
  value->retained_sources = retained;
  value->retained_source_count = source_count;
  return NULL;
}

dort_status_t* dort_value_wrap_owned(
    dort_runtime_t* runtime,
    OrtValue* ort_value,
    dort_value_t** out_value) {
  ONNXType onnx_type = ONNX_TYPE_UNKNOWN;
  int has_value = 0;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  if (out_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_wrap",
        "The output value pointer is null.");
  }
  *out_value = NULL;
  if (!dort_runtime_is_valid(runtime) || ort_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_wrap",
        "The runtime or ONNX Runtime value is invalid.");
  }
  status = dort_runtime_require_api(
      runtime, DORT_API_FEATURE_COMPOSITE_VALUE, "value_wrap");
  if (status != NULL) {
    return status;
  }
  ort_status = dort_runtime_api(runtime)->HasValue(ort_value, &has_value);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        runtime, ort_status, DORT_ERROR_VALUE_KIND_UNSUPPORTED, "value_wrap");
  }
  if (has_value != 0 && has_value != 1) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        0,
        "value_wrap",
        "ONNX Runtime returned a non-Boolean value-presence result.");
  }
  if (has_value == 0) {
    status = dort_optional_none_create(runtime, out_value);
    if (status == NULL) {
      dort_runtime_api(runtime)->ReleaseValue(ort_value);
    }
    return status;
  }
  ort_status = dort_runtime_api(runtime)->GetValueType(ort_value, &onnx_type);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        runtime, ort_status, DORT_ERROR_VALUE_KIND_UNSUPPORTED, "value_wrap");
  }
  if (onnx_type != ONNX_TYPE_TENSOR && onnx_type != ONNX_TYPE_SEQUENCE &&
      onnx_type != ONNX_TYPE_MAP) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        0,
        "value_wrap",
        "The ONNX Runtime value kind is unsupported by this ABI.");
  }
  return dort_value_allocate(
      runtime, ort_value, (uint32_t)onnx_type, 1u, NULL, out_value);
}

dort_status_t* dort_value_wrap_optional_output(
    dort_runtime_t* runtime,
    OrtValue* ort_value,
    dort_value_t** out_value) {
  int has_value = 0;
  OrtStatus* ort_status = NULL;
  dort_value_t* contained = NULL;
  dort_status_t* status = NULL;
  if (out_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_wrap_optional",
        "The output optional pointer is null.");
  }
  *out_value = NULL;
  if (!dort_runtime_is_valid(runtime) || ort_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_wrap_optional",
        "The runtime or ONNX Runtime optional value is invalid.");
  }
  status = dort_runtime_require_api(
      runtime, DORT_API_FEATURE_COMPOSITE_VALUE, "value_wrap_optional");
  if (status != NULL) {
    dort_runtime_api(runtime)->ReleaseValue(ort_value);
    return status;
  }
  ort_status = dort_runtime_api(runtime)->HasValue(ort_value, &has_value);
  if (ort_status != NULL) {
    status = dort_status_from_ort(
        runtime,
        ort_status,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        "value_wrap_optional");
    dort_runtime_api(runtime)->ReleaseValue(ort_value);
    return status;
  }
  if (has_value != 0 && has_value != 1) {
    status = dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        0,
        "value_wrap_optional",
        "ONNX Runtime returned a non-Boolean optional-presence value.");
    dort_runtime_api(runtime)->ReleaseValue(ort_value);
    return status;
  }
  if (has_value == 0) {
    status = dort_optional_none_create(runtime, out_value);
    dort_runtime_api(runtime)->ReleaseValue(ort_value);
    return status;
  }
  status = dort_value_wrap_owned(runtime, ort_value, &contained);
  if (status != NULL) {
    dort_runtime_api(runtime)->ReleaseValue(ort_value);
    return status;
  }
  status = dort_optional_some_create(contained, out_value);
  dort_value_release(contained);
  return status;
}

dort_status_t* DORT_CALL dort_tensor_create_with_buffer(
    dort_runtime_t* runtime,
    dort_buffer_t* buffer,
    size_t byte_offset,
    size_t byte_length,
    const int64_t* dimensions,
    size_t dimension_count,
    uint32_t element_type,
    dort_value_t** out_value) {
  const OrtApi* api = NULL;
  OrtMemoryInfo* memory_info = NULL;
  OrtValue* ort_value = NULL;
  dort_value_t* value = NULL;
  dort_status_t* status = NULL;
  OrtStatus* ort_status = NULL;

  if (out_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_create",
        "The output tensor pointer is null.");
  }
  *out_value = NULL;
  if (!dort_runtime_is_valid(runtime) || !dort_buffer_is_valid(buffer)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_create",
        "The runtime or native buffer handle is invalid.");
  }
  if (!dort_runtime_has_same_identity(runtime, buffer->runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_RUNTIME_IDENTITY_MISMATCH,
        0,
        "tensor_create",
        "The native buffer belongs to a different runtime identity.");
  }
  if (byte_offset > buffer->byte_length ||
      byte_length > buffer->byte_length - byte_offset) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "tensor_create",
        "The tensor byte range is outside its native buffer.");
  }
  {
    size_t element_width = dort_element_width(element_type);
    uintptr_t address = (uintptr_t)((uint8_t*)buffer->data + byte_offset);
    if (element_width != 0u &&
        (byte_offset % element_width != 0u ||
         address % element_width != 0u)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_TENSOR_INVALID,
          0,
          "tensor_create",
          "The tensor byte offset is not aligned for its element width.");
    }
  }
  status = dort_validate_tensor_layout(
      byte_length, dimensions, dimension_count, element_type);
  if (status != NULL) {
    return status;
  }
  status = dort_runtime_require_api(
      runtime, DORT_API_FEATURE_TENSOR_CREATE, "tensor_create");
  if (status != NULL) {
    return status;
  }
  dort_buffer_retain(buffer);
  status = dort_buffer_attach_tensor(
      buffer, byte_offset, byte_length, element_type);
  if (status != NULL) {
    dort_buffer_release(buffer);
    return status;
  }
  api = dort_runtime_api(runtime);
  ort_status = api->CreateCpuMemoryInfo(
      OrtArenaAllocator, OrtMemTypeDefault, &memory_info);
  if (ort_status != NULL || memory_info == NULL) {
    if (memory_info != NULL) {
      api->ReleaseMemoryInfo(memory_info);
    }
    dort_buffer_detach_tensor(buffer);
    dort_buffer_release(buffer);
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_TENSOR_INVALID,
                     0,
                     "tensor_create",
                     "ONNX Runtime did not create CPU memory information.")
               : dort_status_from_ort(
                     runtime,
                     ort_status,
                     DORT_ERROR_TENSOR_INVALID,
                     "tensor_create");
  }
  ort_status = api->CreateTensorWithDataAsOrtValue(
      memory_info,
      (uint8_t*)buffer->data + byte_offset,
      byte_length,
      dimensions,
      dimension_count,
      (ONNXTensorElementDataType)element_type,
      &ort_value);
  api->ReleaseMemoryInfo(memory_info);
  if (ort_status != NULL || ort_value == NULL) {
    if (ort_value != NULL) {
      api->ReleaseValue(ort_value);
    }
    dort_buffer_detach_tensor(buffer);
    dort_buffer_release(buffer);
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_TENSOR_INVALID,
                     0,
                     "tensor_create",
                     "ONNX Runtime did not create the dense tensor value.")
               : dort_status_from_ort(
                     runtime,
                     ort_status,
                     DORT_ERROR_TENSOR_INVALID,
                     "tensor_create");
  }
  status = dort_value_wrap_owned(runtime, ort_value, &value);
  if (status != NULL) {
    api->ReleaseValue(ort_value);
    dort_buffer_detach_tensor(buffer);
    dort_buffer_release(buffer);
    return status;
  }
  value->backing_buffer = buffer;
  *out_value = value;
  return NULL;
}

dort_status_t* DORT_CALL dort_tensor_create_copy(
    dort_runtime_t* runtime,
    const void* data,
    size_t byte_length,
    const int64_t* dimensions,
    size_t dimension_count,
    uint32_t element_type,
    dort_value_t** out_value) {
  dort_buffer_t* buffer = NULL;
  dort_status_t* status = NULL;
  if (out_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_create",
        "The output tensor pointer is null.");
  }
  *out_value = NULL;
  if (byte_length > 0u && data == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_create",
        "A non-empty tensor requires input bytes.");
  }
  status = dort_validate_tensor_layout(
      byte_length, dimensions, dimension_count, element_type);
  if (status != NULL) {
    return status;
  }
  status = dort_buffer_allocate(runtime, byte_length, 64u, &buffer);
  if (status != NULL) {
    return status;
  }
  status = dort_buffer_write(buffer, 0u, data, byte_length);
  if (status == NULL) {
    status = dort_tensor_create_with_buffer(
        runtime,
        buffer,
        0u,
        byte_length,
        dimensions,
        dimension_count,
        element_type,
        out_value);
  }
  dort_buffer_release(buffer);
  return status;
}

dort_status_t* DORT_CALL dort_tensor_create_strings_copy(
    dort_runtime_t* runtime,
    const dort_utf8_span_t* strings,
    size_t string_count,
    const int64_t* dimensions,
    size_t dimension_count,
    dort_value_t** out_value) {
  const OrtApi* api = NULL;
  OrtAllocator* allocator = NULL;
  OrtValue* ort_value = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  size_t expected_count = 0u;
  size_t total_bytes = 0u;
  size_t index = 0u;
  if (out_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "string_tensor_create",
        "The output string-tensor pointer is null.");
  }
  *out_value = NULL;
  if (!dort_runtime_is_valid(runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "string_tensor_create",
        "The runtime handle is null or invalid.");
  }
  status = dort_validate_tensor_shape(
      dimensions,
      dimension_count,
      &expected_count,
      "string_tensor_create");
  if (status != NULL) {
    return status;
  }
  if (string_count != expected_count ||
      (string_count == 0u) != (strings == NULL)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_TENSOR_INVALID,
        0,
        "string_tensor_create",
        "The string array count does not match the concrete tensor shape.");
  }
  if (string_count > DORT_MAX_STRING_ELEMENTS) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "string_tensor_create",
        "The string tensor exceeds the element-count limit.");
  }
  for (index = 0u; index < string_count; ++index) {
    const dort_utf8_span_t* string_value = &strings[index];
    int validation = DORT_ERROR_NONE;
    if (string_value->struct_size < DORT_UTF8_SPAN_V1_SIZE ||
        string_value->reserved0 != 0u ||
        (string_value->length > 0u && string_value->data == NULL)) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "string_tensor_create",
          "A string span has an invalid ABI prefix, reserved field, or pointer/length pair.");
    }
    if (string_value->length > DORT_MAX_STRING_ELEMENT_BYTES ||
        !dort_checked_add_size(
            total_bytes, string_value->length, &total_bytes) ||
        total_bytes > DORT_MAX_STRING_TENSOR_BYTES) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_LIMIT_EXCEEDED,
          0,
          "string_tensor_create",
          "The string tensor exceeds its per-element or total byte limit.");
    }
    validation = dort_validate_utf8_bytes(
        string_value->data, string_value->length, 1);
    if (validation != DORT_ERROR_NONE) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          validation,
          0,
          "string_tensor_create",
          "A string tensor element is invalid UTF-8 or contains an embedded NUL.");
    }
  }
  status = dort_runtime_require_api(
      runtime, DORT_API_FEATURE_STRING_TENSOR, "string_tensor_create");
  if (status != NULL) {
    return status;
  }
  api = dort_runtime_api(runtime);
  ort_status = api->GetAllocatorWithDefaultOptions(&allocator);
  if (ort_status != NULL || allocator == NULL) {
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_ORT_API_UNAVAILABLE,
                     0,
                     "string_tensor_create",
                     "ONNX Runtime did not return its default allocator.")
               : dort_status_from_ort(
                     runtime,
                     ort_status,
                     DORT_ERROR_TENSOR_INVALID,
                     "string_tensor_create");
  }
  ort_status = api->CreateTensorAsOrtValue(
      allocator,
      dimensions,
      dimension_count,
      ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING,
      &ort_value);
  if (ort_status != NULL || ort_value == NULL) {
    if (ort_value != NULL) {
      api->ReleaseValue(ort_value);
    }
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_TENSOR_INVALID,
                     0,
                     "string_tensor_create",
                     "ONNX Runtime did not create the string tensor.")
               : dort_status_from_ort(
                     runtime,
                     ort_status,
                     DORT_ERROR_TENSOR_INVALID,
                     "string_tensor_create");
  }
  for (index = 0u; index < string_count; ++index) {
    char* destination = NULL;
    ort_status = api->GetResizedStringTensorElementBuffer(
        ort_value, index, strings[index].length, &destination);
    if (ort_status != NULL ||
        (strings[index].length > 0u && destination == NULL)) {
      if (ort_status != NULL) {
        status = dort_status_from_ort(
            runtime,
            ort_status,
            DORT_ERROR_TENSOR_INVALID,
            "string_tensor_create");
      } else {
        status = dort_status_create(
            DORT_ERROR_DOMAIN_ORT_API,
            DORT_ERROR_TENSOR_INVALID,
            0,
            "string_tensor_create",
            "ONNX Runtime returned null storage for a non-empty string element.");
      }
      api->ReleaseValue(ort_value);
      return status;
    }
    if (strings[index].length > 0u) {
      memcpy(destination, strings[index].data, strings[index].length);
    }
  }
  status = dort_value_wrap_owned(runtime, ort_value, out_value);
  if (status != NULL) {
    api->ReleaseValue(ort_value);
  }
  return status;
}

void DORT_CALL dort_value_retain(dort_value_t* value) {
  unsigned int references = 0u;
  if (!dort_value_is_valid(value)) {
    return;
  }
  references = atomic_load_explicit(&value->reference_count, memory_order_relaxed);
  while (references != 0u && references != UINT32_MAX) {
    if (atomic_compare_exchange_weak_explicit(
            &value->reference_count,
            &references,
            references + 1u,
            memory_order_relaxed,
            memory_order_relaxed)) {
      return;
    }
  }
}

void DORT_CALL dort_value_release(dort_value_t* value) {
  unsigned int previous = 0u;
  if (!dort_value_is_valid(value)) {
    return;
  }
  previous = atomic_fetch_sub_explicit(
      &value->reference_count, 1u, memory_order_acq_rel);
  if (previous == 1u) {
    dort_runtime_t* runtime = value->runtime;
    dort_buffer_t* backing_buffer = value->backing_buffer;
    dort_value_t* optional_contained = value->optional_contained;
    dort_value_t** retained_sources = value->retained_sources;
    size_t retained_source_count = value->retained_source_count;
    size_t index = 0u;
    OrtValue* ort_value = value->value;
    value->magic = 0u;
    value->runtime = NULL;
    value->value = NULL;
    value->backing_buffer = NULL;
    value->optional_contained = NULL;
    value->retained_sources = NULL;
    value->retained_source_count = 0u;
    value->kind = 0u;
    value->nesting_depth = 0u;
    if (ort_value != NULL) {
      dort_runtime_api(runtime)->ReleaseValue(ort_value);
    }
    dort_buffer_detach_tensor(backing_buffer);
    dort_buffer_release(backing_buffer);
    dort_value_release(optional_contained);
    for (index = 0u; index < retained_source_count; ++index) {
      dort_value_release(retained_sources[index]);
    }
    free(retained_sources);
    free(value);
    dort_runtime_release(runtime);
  }
}

dort_runtime_t* dort_value_runtime(const dort_value_t* value) {
  return dort_value_is_valid(value) ? value->runtime : NULL;
}

OrtValue* dort_value_ort(const dort_value_t* value) {
  return dort_value_is_valid(value) ? value->value : NULL;
}

OrtValue* dort_value_ort_for_run(const dort_value_t* value) {
  if (!dort_value_is_valid(value)) {
    return NULL;
  }
  if (value->kind == DORT_VALUE_KIND_OPTIONAL) {
    return value->optional_contained == NULL
               ? NULL
               : dort_value_ort_for_run(value->optional_contained);
  }
  return value->value;
}

int dort_value_is_optional_none(const dort_value_t* value) {
  return dort_value_is_valid(value) &&
         value->kind == DORT_VALUE_KIND_OPTIONAL &&
         value->optional_contained == NULL;
}

dort_status_t* DORT_CALL dort_value_kind(
    const dort_value_t* value,
    uint32_t* out_kind) {
  if (out_kind == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_kind",
        "The output value-kind pointer is null.");
  }
  *out_kind = 0u;
  if (!dort_value_is_valid(value)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_kind",
        "The value handle is null or invalid.");
  }
  *out_kind = value->kind;
  return NULL;
}

dort_status_t* DORT_CALL dort_optional_none_create(
    dort_runtime_t* runtime,
    dort_value_t** out_value) {
  return dort_value_allocate(
      runtime, NULL, DORT_VALUE_KIND_OPTIONAL, 1u, NULL, out_value);
}

dort_status_t* DORT_CALL dort_optional_some_create(
    const dort_value_t* contained_value,
    dort_value_t** out_value) {
  if (out_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "optional_create",
        "The output optional pointer is null.");
  }
  *out_value = NULL;
  if (!dort_value_is_valid(contained_value) ||
      contained_value->kind == DORT_VALUE_KIND_OPTIONAL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "optional_create",
        "An optional Some value requires one valid non-optional contained value.");
  }
  if (contained_value->nesting_depth >= DORT_MAX_VALUE_NESTING_DEPTH) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "optional_create",
        "The optional value would exceed the nesting-depth limit.");
  }
  return dort_value_allocate(
      contained_value->runtime,
      NULL,
      DORT_VALUE_KIND_OPTIONAL,
      contained_value->nesting_depth + 1u,
      (dort_value_t*)contained_value,
      out_value);
}

dort_status_t* DORT_CALL dort_sequence_create(
    dort_runtime_t* runtime,
    const dort_value_t* const* values,
    size_t value_count,
    dort_value_t** out_value) {
  const OrtApi* api = NULL;
  const OrtValue** ort_values = NULL;
  OrtValue* ort_value = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  size_t index = 0u;
  uint32_t maximum_depth = 0u;
  if (out_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "sequence_create",
        "The output sequence pointer is null.");
  }
  *out_value = NULL;
  if (!dort_runtime_is_valid(runtime) || values == NULL || value_count == 0u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "sequence_create",
        "A sequence requires a valid runtime and at least one value.");
  }
  if (value_count > DORT_MAX_COMPOSITE_CHILDREN) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "sequence_create",
        "The sequence exceeds the composite child-count limit.");
  }
  status = dort_runtime_require_api(
      runtime, DORT_API_FEATURE_COMPOSITE_VALUE, "sequence_create");
  if (status != NULL) {
    return status;
  }
  ort_values = (const OrtValue**)calloc(value_count, sizeof(*ort_values));
  if (ort_values == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "sequence_create",
        "Could not allocate the bounded sequence input array.");
  }
  for (index = 0u; index < value_count; ++index) {
    const dort_value_t* child = values[index];
    if (!dort_value_is_valid(child)) {
      free(ort_values);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_INVALID_ARGUMENT,
          0,
          "sequence_create",
          "A sequence child handle is null or invalid.");
    }
    if (!dort_runtime_has_same_identity(runtime, child->runtime)) {
      free(ort_values);
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_RUNTIME_IDENTITY_MISMATCH,
          0,
          "sequence_create",
          "A sequence child belongs to a different runtime identity.");
    }
    if (child->kind == DORT_VALUE_KIND_OPTIONAL || child->value == NULL) {
      free(ort_values);
      return dort_status_create(
          DORT_ERROR_DOMAIN_UNSUPPORTED,
          DORT_ERROR_VALUE_KIND_UNSUPPORTED,
          0,
          "sequence_create",
          "API 27 cannot construct a sequence from synthetic optional values.");
    }
    status = dort_value_validate_supported(child, "sequence_create");
    if (status != NULL) {
      free(ort_values);
      return status;
    }
    if (child->nesting_depth > maximum_depth) {
      maximum_depth = child->nesting_depth;
    }
    ort_values[index] = child->value;
  }
  if (maximum_depth >= DORT_MAX_VALUE_NESTING_DEPTH) {
    free(ort_values);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "sequence_create",
        "The sequence would exceed the value nesting-depth limit.");
  }
  api = dort_runtime_api(runtime);
  ort_status = api->CreateValue(
      ort_values, value_count, ONNX_TYPE_SEQUENCE, &ort_value);
  free(ort_values);
  if (ort_status != NULL || ort_value == NULL) {
    if (ort_value != NULL) {
      api->ReleaseValue(ort_value);
    }
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_VALUE_KIND_UNSUPPORTED,
                     0,
                     "sequence_create",
                     "ONNX Runtime did not create the sequence value.")
               : dort_status_from_ort(
                     runtime,
                     ort_status,
                     DORT_ERROR_VALUE_KIND_UNSUPPORTED,
                     "sequence_create");
  }
  status = dort_value_allocate(
      runtime,
      ort_value,
      DORT_VALUE_KIND_SEQUENCE,
      maximum_depth + 1u,
      NULL,
      out_value);
  if (status == NULL) {
    status = dort_value_attach_sources(
        *out_value, values, value_count, "sequence_create");
    if (status != NULL) {
      dort_value_release(*out_value);
      *out_value = NULL;
    }
  } else {
    api->ReleaseValue(ort_value);
  }
  return status;
}

dort_status_t* DORT_CALL dort_map_create(
    dort_runtime_t* runtime,
    const dort_value_t* keys,
    const dort_value_t* values,
    dort_value_t** out_value) {
  const OrtApi* api = NULL;
  const OrtValue* ort_values[2];
  OrtValue* ort_value = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  ONNXTensorElementDataType key_element_type =
      ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  ONNXTensorElementDataType value_element_type =
      ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  int64_t key_dimensions[DORT_MAX_TENSOR_RANK];
  int64_t value_dimensions[DORT_MAX_TENSOR_RANK];
  size_t key_dimension_count = 0u;
  size_t value_dimension_count = 0u;
  size_t key_count = 0u;
  size_t ignored_bytes = 0u;
  uint32_t maximum_depth = 0u;
  if (out_value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "map_create",
        "The output map pointer is null.");
  }
  *out_value = NULL;
  if (!dort_runtime_is_valid(runtime) || !dort_value_is_valid(keys) ||
      !dort_value_is_valid(values) ||
      !dort_runtime_has_same_identity(runtime, keys->runtime) ||
      !dort_runtime_has_same_identity(runtime, values->runtime)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        dort_value_is_valid(keys) && dort_value_is_valid(values)
            ? DORT_ERROR_RUNTIME_IDENTITY_MISMATCH
            : DORT_ERROR_INVALID_ARGUMENT,
        0,
        "map_create",
        "Map keys and values must be valid values from the same runtime identity.");
  }
  if (keys->kind != DORT_VALUE_KIND_TENSOR ||
      values->kind != DORT_VALUE_KIND_TENSOR || keys->value == NULL ||
      values->value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        0,
        "map_create",
        "API 27 map construction requires concrete key and value tensors.");
  }
  status = dort_runtime_require_api(
      runtime, DORT_API_FEATURE_COMPOSITE_VALUE, "map_create");
  if (status == NULL) {
    status = dort_value_validate_supported(keys, "map_create");
  }
  if (status == NULL) {
    status = dort_value_validate_supported(values, "map_create");
  }
  if (status != NULL) {
    return status;
  }
  status = dort_query_tensor_element_type(
      keys, &key_element_type, "map_create");
  if (status != NULL) {
    return status;
  }
  if (key_element_type == ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING) {
    status = dort_query_string_tensor(
        keys,
        &key_count,
        &ignored_bytes,
        key_dimensions,
        &key_dimension_count,
        "map_create");
  } else if (key_element_type == ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64) {
    status = dort_query_fixed_tensor(
        keys,
        &key_element_type,
        key_dimensions,
        &key_dimension_count,
        &ignored_bytes,
        "map_create");
    if (status == NULL && key_dimension_count == 1u) {
      key_count = (size_t)key_dimensions[0];
    }
  } else {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        0,
        "map_create",
        "Map keys must be a rank-one string or int64 tensor.");
  }
  if (status != NULL) {
    return status;
  }
  if (key_dimension_count != 1u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_TENSOR_INVALID,
        0,
        "map_create",
        "Map keys must be a rank-one string or int64 tensor.");
  }
  status = dort_query_tensor_element_type(
      values, &value_element_type, "map_create");
  if (status != NULL) {
    return status;
  }
  if (value_element_type == ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING) {
    size_t ignored_count = 0u;
    status = dort_query_string_tensor(
        values,
        &ignored_count,
        &ignored_bytes,
        value_dimensions,
        &value_dimension_count,
        "map_create");
  } else if (dort_element_width((uint32_t)value_element_type) != 0u) {
    status = dort_query_fixed_tensor(
        values,
        &value_element_type,
        value_dimensions,
        &value_dimension_count,
        &ignored_bytes,
        "map_create");
  } else {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        0,
        "map_create",
        "Map values use an unsupported tensor element type.");
  }
  if (status != NULL) {
    return status;
  }
  if (value_dimension_count == 0u ||
      (uint64_t)value_dimensions[0] != (uint64_t)key_count) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_TENSOR_INVALID,
        0,
        "map_create",
        "The map value tensor's leading cardinality must match the key count.");
  }
  maximum_depth = keys->nesting_depth > values->nesting_depth
                      ? keys->nesting_depth
                      : values->nesting_depth;
  if (maximum_depth >= DORT_MAX_VALUE_NESTING_DEPTH) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "map_create",
        "The map would exceed the value nesting-depth limit.");
  }
  ort_values[0] = keys->value;
  ort_values[1] = values->value;
  api = dort_runtime_api(runtime);
  ort_status = api->CreateValue(ort_values, 2u, ONNX_TYPE_MAP, &ort_value);
  if (ort_status != NULL || ort_value == NULL) {
    if (ort_value != NULL) {
      api->ReleaseValue(ort_value);
    }
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_VALUE_KIND_UNSUPPORTED,
                     0,
                     "map_create",
                     "ONNX Runtime did not create the map value.")
               : dort_status_from_ort(
                     runtime,
                     ort_status,
                     DORT_ERROR_VALUE_KIND_UNSUPPORTED,
                     "map_create");
  }
  status = dort_value_allocate(
      runtime,
      ort_value,
      DORT_VALUE_KIND_MAP,
      maximum_depth + 1u,
      NULL,
      out_value);
  if (status == NULL) {
    const dort_value_t* sources[2];
    sources[0] = keys;
    sources[1] = values;
    status = dort_value_attach_sources(
        *out_value, sources, 2u, "map_create");
    if (status != NULL) {
      dort_value_release(*out_value);
      *out_value = NULL;
    }
  } else {
    api->ReleaseValue(ort_value);
  }
  return status;
}

dort_status_t* DORT_CALL dort_value_child_count(
    const dort_value_t* value,
    size_t* out_count) {
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  if (out_count == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_child_count",
        "The output child-count pointer is null.");
  }
  *out_count = 0u;
  if (!dort_value_is_valid(value)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_child_count",
        "The value handle is null or invalid.");
  }
  if (value->kind == DORT_VALUE_KIND_OPTIONAL) {
    *out_count = value->optional_contained == NULL ? 0u : 1u;
    return NULL;
  }
  if (value->kind != DORT_VALUE_KIND_SEQUENCE &&
      value->kind != DORT_VALUE_KIND_MAP) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_NOT_COMPOSITE,
        0,
        "value_child_count",
        "Tensor values do not expose composite children.");
  }
  status = dort_runtime_require_api(
      value->runtime, DORT_API_FEATURE_COMPOSITE_VALUE, "value_child_count");
  if (status != NULL) {
    return status;
  }
  ort_status = dort_runtime_api(value->runtime)->GetValueCount(
      value->value, out_count);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        value->runtime,
        ort_status,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        "value_child_count");
  }
  if (*out_count > DORT_MAX_COMPOSITE_CHILDREN ||
      (value->kind == DORT_VALUE_KIND_MAP && *out_count != 2u)) {
    *out_count = 0u;
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "value_child_count",
        "ONNX Runtime returned an invalid or oversized composite child count.");
  }
  return NULL;
}

dort_status_t* DORT_CALL dort_value_child_get(
    const dort_value_t* value,
    size_t index,
    dort_value_t** out_child) {
  const OrtApi* api = NULL;
  OrtAllocator* allocator = NULL;
  OrtValue* ort_child = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  size_t count = 0u;
  if (out_child == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "value_child_get",
        "The output child pointer is null.");
  }
  *out_child = NULL;
  status = dort_value_child_count(value, &count);
  if (status != NULL) {
    return status;
  }
  if (index >= count) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "value_child_get",
        "The composite child index is outside the value range.");
  }
  if (value->kind == DORT_VALUE_KIND_OPTIONAL) {
    dort_value_retain(value->optional_contained);
    *out_child = value->optional_contained;
    return NULL;
  }
  api = dort_runtime_api(value->runtime);
  ort_status = api->GetAllocatorWithDefaultOptions(&allocator);
  if (ort_status == NULL && allocator == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_ORT_API_UNAVAILABLE,
        0,
        "value_child_get",
        "ONNX Runtime did not return its default allocator.");
  }
  if (ort_status == NULL) {
    ort_status = api->GetValue(value->value, (int)index, allocator, &ort_child);
  }
  if (ort_status != NULL || ort_child == NULL) {
    if (ort_child != NULL) {
      api->ReleaseValue(ort_child);
    }
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_VALUE_KIND_UNSUPPORTED,
                     0,
                     "value_child_get",
                     "ONNX Runtime did not return the requested composite child.")
               : dort_status_from_ort(
                     value->runtime,
                     ort_status,
                     DORT_ERROR_VALUE_KIND_UNSUPPORTED,
                     "value_child_get");
  }
  status = dort_value_wrap_owned(value->runtime, ort_child, out_child);
  if (status == NULL) {
    status = dort_value_validate_supported(*out_child, "value_child_get");
  }
  if (status != NULL) {
    if (*out_child != NULL) {
      dort_value_release(*out_child);
      *out_child = NULL;
    } else {
      api->ReleaseValue(ort_child);
    }
  }
  return status;
}

static dort_status_t* dort_query_tensor_element_type(
    const dort_value_t* value,
    ONNXTensorElementDataType* out_element_type,
    const char* operation) {
  const OrtApi* api = NULL;
  OrtTensorTypeAndShapeInfo* info = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  *out_element_type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  if (!dort_value_is_valid(value) ||
      value->kind != DORT_VALUE_KIND_TENSOR || value->value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_NOT_TENSOR,
        0,
        operation,
        "The value is not a valid tensor handle.");
  }
  status = dort_runtime_require_api(
      value->runtime, DORT_API_FEATURE_TENSOR_READ, operation);
  if (status != NULL) {
    return status;
  }
  api = dort_runtime_api(value->runtime);
  ort_status = api->GetTensorTypeAndShape(value->value, &info);
  if (ort_status == NULL && info == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_TENSOR_INVALID,
        0,
        operation,
        "ONNX Runtime returned null tensor type-and-shape information.");
  }
  if (ort_status == NULL) {
    ort_status = api->GetTensorElementType(info, out_element_type);
  }
  if (info != NULL) {
    api->ReleaseTensorTypeAndShapeInfo(info);
  }
  if (ort_status != NULL) {
    return dort_status_from_ort(
        value->runtime, ort_status, DORT_ERROR_NOT_TENSOR, operation);
  }
  return NULL;
}

static dort_status_t* dort_query_fixed_tensor(
    const dort_value_t* value,
    ONNXTensorElementDataType* out_element_type,
    int64_t* out_dimensions,
    size_t* out_dimension_count,
    size_t* out_byte_length,
    const char* operation) {
  const OrtApi* api = NULL;
  OrtTensorTypeAndShapeInfo* info = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  size_t element_count = 1u;
  size_t expected_bytes = 0u;
  size_t width = 0u;
  size_t index = 0u;
  *out_element_type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  *out_dimension_count = 0u;
  *out_byte_length = 0u;
  if (!dort_value_is_valid(value) ||
      value->kind != DORT_VALUE_KIND_TENSOR || value->value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_NOT_TENSOR,
        0,
        operation,
        "The value is not a valid tensor handle.");
  }
  status = dort_runtime_require_api(
      value->runtime, DORT_API_FEATURE_TENSOR_READ, operation);
  if (status != NULL) {
    return status;
  }
  api = dort_runtime_api(value->runtime);
  ort_status = api->GetTensorTypeAndShape(value->value, &info);
  if (ort_status == NULL && info == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_TENSOR_INVALID,
        0,
        operation,
        "ONNX Runtime returned null tensor type-and-shape information.");
  }
  if (ort_status == NULL) {
    ort_status = api->GetTensorElementType(info, out_element_type);
  }
  if (ort_status == NULL) {
    ort_status = api->GetDimensionsCount(info, out_dimension_count);
  }
  if (ort_status == NULL && *out_dimension_count > DORT_MAX_TENSOR_RANK) {
    api->ReleaseTensorTypeAndShapeInfo(info);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        operation,
        "The output tensor rank exceeds the ABI limit.");
  }
  if (ort_status == NULL && *out_dimension_count > 0u) {
    ort_status = api->GetDimensions(
        info, out_dimensions, *out_dimension_count);
  }
  if (ort_status == NULL) {
    ort_status = api->GetTensorSizeInBytes(value->value, out_byte_length);
  }
  if (info != NULL) {
    api->ReleaseTensorTypeAndShapeInfo(info);
  }
  if (ort_status != NULL) {
    return dort_status_from_ort(
        value->runtime, ort_status, DORT_ERROR_NOT_TENSOR, operation);
  }
  width = dort_element_width((uint32_t)*out_element_type);
  if (width == 0u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_TENSOR_INVALID,
        0,
        operation,
        "The output is not a supported fixed-width tensor.");
  }
  for (index = 0u; index < *out_dimension_count; ++index) {
    size_t dimension = 0u;
    if (out_dimensions[index] < 0 ||
        (uint64_t)out_dimensions[index] >
            (uint64_t)DORT_MAX_TENSOR_DIMENSION) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_LIMIT_EXCEEDED,
          0,
          operation,
          "An output tensor dimension is unresolved or exceeds the ABI limit.");
    }
    dimension = (size_t)out_dimensions[index];
    if (!dort_checked_multiply_size(
            element_count, dimension, &element_count) ||
        element_count > DORT_MAX_TENSOR_ELEMENTS) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_LIMIT_EXCEEDED,
          0,
          operation,
          "The output tensor element count exceeds the ABI limit.");
    }
  }
  if (!dort_checked_multiply_size(element_count, width, &expected_bytes) ||
      expected_bytes > DORT_MAX_TENSOR_BYTES ||
      expected_bytes != *out_byte_length) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_TENSOR_INVALID,
        0,
        operation,
        "ONNX Runtime returned inconsistent or oversized tensor byte metadata.");
  }
  return NULL;
}

static dort_status_t* dort_require_cpu_tensor_memory(
    const dort_value_t* value,
    const char* operation) {
  const OrtApi* api = NULL;
  const OrtMemoryInfo* memory_info = NULL;
  OrtMemoryInfoDeviceType device_type = OrtMemoryInfoDeviceType_CPU;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = dort_runtime_require_api(
      value->runtime, DORT_API_FEATURE_DATA_LEASE, operation);
  if (status != NULL) {
    return status;
  }
  api = dort_runtime_api(value->runtime);
  ort_status = api->GetTensorMemoryInfo(value->value, &memory_info);
  if (ort_status != NULL || memory_info == NULL) {
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_MEMORY_DOMAIN_UNSUPPORTED,
                     0,
                     operation,
                     "ONNX Runtime did not report the tensor memory domain.")
               : dort_status_from_ort(
                     value->runtime,
                     ort_status,
                     DORT_ERROR_MEMORY_DOMAIN_UNSUPPORTED,
                     operation);
  }
  api->MemoryInfoGetDeviceType(memory_info, &device_type);
  if (device_type != OrtMemoryInfoDeviceType_CPU) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_MEMORY_DOMAIN_UNSUPPORTED,
        0,
        operation,
        "The tensor is not resident in CPU-host-accessible memory.");
  }
  return NULL;
}

static dort_status_t* dort_query_string_tensor(
    const dort_value_t* value,
    size_t* out_string_count,
    size_t* out_total_bytes,
    int64_t* out_dimensions,
    size_t* out_dimension_count,
    const char* operation) {
  const OrtApi* api = NULL;
  OrtTensorTypeAndShapeInfo* info = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  ONNXTensorElementDataType element_type =
      ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  int64_t dimensions[DORT_MAX_TENSOR_RANK];
  size_t dimension_count = 0u;
  size_t element_count = 0u;
  *out_string_count = 0u;
  *out_total_bytes = 0u;
  if (out_dimension_count != NULL) {
    *out_dimension_count = 0u;
  }
  if (!dort_value_is_valid(value) ||
      value->kind != DORT_VALUE_KIND_TENSOR || value->value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_NOT_TENSOR,
        0,
        operation,
        "The value is not a valid tensor handle.");
  }
  status = dort_runtime_require_api(
      value->runtime, DORT_API_FEATURE_STRING_TENSOR, operation);
  if (status != NULL) {
    return status;
  }
  api = dort_runtime_api(value->runtime);
  ort_status = api->GetTensorTypeAndShape(value->value, &info);
  if (ort_status == NULL && info == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_TENSOR_INVALID,
        0,
        operation,
        "ONNX Runtime returned null string tensor type-and-shape information.");
  }
  if (ort_status == NULL) {
    ort_status = api->GetTensorElementType(info, &element_type);
  }
  if (ort_status == NULL) {
    ort_status = api->GetDimensionsCount(info, &dimension_count);
  }
  if (ort_status == NULL && dimension_count > DORT_MAX_TENSOR_RANK) {
    api->ReleaseTensorTypeAndShapeInfo(info);
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        operation,
        "The string tensor rank exceeds the ABI limit.");
  }
  if (ort_status == NULL && dimension_count > 0u) {
    ort_status = api->GetDimensions(info, dimensions, dimension_count);
  }
  if (info != NULL) {
    api->ReleaseTensorTypeAndShapeInfo(info);
  }
  if (ort_status != NULL) {
    return dort_status_from_ort(
        value->runtime, ort_status, DORT_ERROR_NOT_TENSOR, operation);
  }
  if (element_type != ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_TENSOR_INVALID,
        0,
        operation,
        "The tensor does not have the string element type.");
  }
  status = dort_validate_tensor_shape(
      dimensions, dimension_count, &element_count, operation);
  if (status != NULL) {
    return status;
  }
  if (element_count > DORT_MAX_STRING_ELEMENTS) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        operation,
        "The string tensor exceeds the element-count limit.");
  }
  ort_status = api->GetStringTensorDataLength(value->value, out_total_bytes);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        value->runtime, ort_status, DORT_ERROR_TENSOR_INVALID, operation);
  }
  if (*out_total_bytes > DORT_MAX_STRING_TENSOR_BYTES) {
    *out_total_bytes = 0u;
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        operation,
        "The string tensor exceeds the total UTF-8 byte limit.");
  }
  *out_string_count = element_count;
  if (out_dimensions != NULL && dimension_count > 0u) {
    memcpy(
        out_dimensions,
        dimensions,
        dimension_count * sizeof(*out_dimensions));
  }
  if (out_dimension_count != NULL) {
    *out_dimension_count = dimension_count;
  }
  return NULL;
}

dort_status_t* dort_value_validate_fixed_tensor(
    const dort_value_t* value,
    const char* operation) {
  ONNXTensorElementDataType element_type =
      ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  int64_t dimensions[DORT_MAX_TENSOR_RANK];
  size_t dimension_count = 0u;
  size_t byte_length = 0u;
  const void* data = NULL;
  size_t index = 0u;
  dort_status_t* status = NULL;
  OrtStatus* ort_status = NULL;
  if (!dort_value_is_valid(value)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        operation,
        "The tensor value handle is null or invalid.");
  }
  status = dort_query_fixed_tensor(
      value,
      &element_type,
      dimensions,
      &dimension_count,
      &byte_length,
      operation);
  if (status != NULL || element_type != ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL) {
    return status;
  }
  status = dort_require_cpu_tensor_memory(value, operation);
  if (status != NULL) {
    return status;
  }
  ort_status = dort_runtime_api(value->runtime)->GetTensorData(value->value, &data);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        value->runtime, ort_status, DORT_ERROR_NOT_TENSOR, operation);
  }
  if (byte_length > 0u && data == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_TENSOR_INVALID,
        0,
        operation,
        "ONNX Runtime returned null storage for a non-empty Boolean tensor.");
  }
  for (index = 0u; index < byte_length; ++index) {
    if (((const uint8_t*)data)[index] > 1u) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API,
          DORT_ERROR_TENSOR_INVALID,
          0,
          operation,
          "ONNX Runtime returned a Boolean tensor outside the zero-or-one domain.");
    }
  }
  return NULL;
}

static dort_status_t* dort_validate_string_tensor_contents(
    const dort_value_t* value,
    const char* operation) {
  const OrtApi* api = NULL;
  uint8_t* contents = NULL;
  size_t* offsets = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  size_t string_count = 0u;
  size_t total_bytes = 0u;
  size_t index = 0u;
  status = dort_query_string_tensor(
      value, &string_count, &total_bytes, NULL, NULL, operation);
  if (status != NULL) {
    return status;
  }
  contents = (uint8_t*)malloc(total_bytes == 0u ? 1u : total_bytes);
  if (string_count > 0u) {
    offsets = (size_t*)calloc(string_count, sizeof(*offsets));
  }
  if (contents == NULL || (string_count > 0u && offsets == NULL)) {
    free(contents);
    free(offsets);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        operation,
        "Could not allocate bounded string-tensor validation storage.");
  }
  api = dort_runtime_api(value->runtime);
  ort_status = api->GetStringTensorContent(
      value->value, contents, total_bytes, offsets, string_count);
  if (ort_status != NULL) {
    free(contents);
    free(offsets);
    return dort_status_from_ort(
        value->runtime, ort_status, DORT_ERROR_TENSOR_INVALID, operation);
  }
  if (string_count > 0u && offsets[0] != 0u) {
    free(contents);
    free(offsets);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_TENSOR_INVALID,
        0,
        operation,
        "ONNX Runtime returned an invalid first string-tensor offset.");
  }
  for (index = 0u; index < string_count; ++index) {
    size_t start = offsets[index];
    size_t end = index + 1u < string_count ? offsets[index + 1u] : total_bytes;
    int validation = DORT_ERROR_NONE;
    if (start > end || end > total_bytes ||
        end - start > DORT_MAX_STRING_ELEMENT_BYTES) {
      free(contents);
      free(offsets);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API,
          DORT_ERROR_TENSOR_INVALID,
          0,
          operation,
          "ONNX Runtime returned invalid or oversized string-tensor offsets.");
    }
    validation = dort_validate_utf8_bytes(contents + start, end - start, 1);
    if (validation != DORT_ERROR_NONE) {
      free(contents);
      free(offsets);
      return dort_status_create(
          DORT_ERROR_DOMAIN_ORT_API,
          validation,
          0,
          operation,
          "ONNX Runtime returned invalid UTF-8 or an embedded NUL in a string tensor.");
    }
  }
  free(contents);
  free(offsets);
  return NULL;
}

static dort_status_t* dort_validate_value_recursive(
    const dort_value_t* value,
    uint32_t current_depth,
    size_t* inout_nodes,
    uint32_t* out_tree_depth,
    const char* operation) {
  const OrtApi* api = NULL;
  OrtAllocator* allocator = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  size_t child_count = 0u;
  size_t index = 0u;
  uint32_t maximum_child_depth = 0u;
  *out_tree_depth = 0u;
  if (!dort_value_is_valid(value)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        operation,
        "The value handle is null or invalid.");
  }
  if (current_depth > DORT_MAX_VALUE_NESTING_DEPTH ||
      *inout_nodes >= DORT_MAX_VALUE_NODES) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        operation,
        "The value exceeds the recursive depth or node-count limit.");
  }
  ++*inout_nodes;
  if (value->kind == DORT_VALUE_KIND_OPTIONAL) {
    if (value->optional_contained == NULL) {
      *out_tree_depth = 1u;
      ((dort_value_t*)value)->nesting_depth = 1u;
      return NULL;
    }
    status = dort_validate_value_recursive(
        value->optional_contained,
        current_depth + 1u,
        inout_nodes,
        &maximum_child_depth,
        operation);
    if (status != NULL) {
      return status;
    }
    *out_tree_depth = maximum_child_depth + 1u;
    ((dort_value_t*)value)->nesting_depth = *out_tree_depth;
    return NULL;
  }
  if (value->kind == DORT_VALUE_KIND_TENSOR) {
    ONNXTensorElementDataType element_type =
        ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
    status = dort_query_tensor_element_type(value, &element_type, operation);
    if (status != NULL) {
      return status;
    }
    if (element_type == ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING) {
      status = dort_validate_string_tensor_contents(value, operation);
    } else if (dort_element_width((uint32_t)element_type) == 0u) {
      status = dort_status_create(
          DORT_ERROR_DOMAIN_UNSUPPORTED,
          DORT_ERROR_VALUE_KIND_UNSUPPORTED,
          0,
          operation,
          "The tensor uses an unsupported element type.");
    } else {
      status = dort_value_validate_fixed_tensor(value, operation);
    }
    if (status == NULL) {
      *out_tree_depth = 1u;
      ((dort_value_t*)value)->nesting_depth = 1u;
    }
    return status;
  }
  if (value->kind != DORT_VALUE_KIND_SEQUENCE &&
      value->kind != DORT_VALUE_KIND_MAP) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        0,
        operation,
        "Sparse, opaque, and unknown value kinds are unsupported.");
  }
  status = dort_runtime_require_api(
      value->runtime, DORT_API_FEATURE_COMPOSITE_VALUE, operation);
  if (status != NULL) {
    return status;
  }
  api = dort_runtime_api(value->runtime);
  ort_status = api->GetValueCount(value->value, &child_count);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        value->runtime,
        ort_status,
        DORT_ERROR_VALUE_KIND_UNSUPPORTED,
        operation);
  }
  if (child_count > DORT_MAX_COMPOSITE_CHILDREN ||
      (value->kind == DORT_VALUE_KIND_MAP && child_count != 2u)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        operation,
        "The composite value has an invalid or oversized child count.");
  }
  ort_status = api->GetAllocatorWithDefaultOptions(&allocator);
  if (ort_status != NULL || allocator == NULL) {
    return ort_status == NULL
               ? dort_status_create(
                     DORT_ERROR_DOMAIN_ORT_API,
                     DORT_ERROR_ORT_API_UNAVAILABLE,
                     0,
                     operation,
                     "ONNX Runtime did not return its default allocator.")
               : dort_status_from_ort(
                     value->runtime,
                     ort_status,
                     DORT_ERROR_VALUE_KIND_UNSUPPORTED,
                     operation);
  }
  for (index = 0u; index < child_count; ++index) {
    OrtValue* ort_child = NULL;
    dort_value_t* child = NULL;
    uint32_t child_depth = 0u;
    ort_status = api->GetValue(value->value, (int)index, allocator, &ort_child);
    if (ort_status != NULL || ort_child == NULL) {
      if (ort_child != NULL) {
        api->ReleaseValue(ort_child);
      }
      return ort_status == NULL
                 ? dort_status_create(
                       DORT_ERROR_DOMAIN_ORT_API,
                       DORT_ERROR_VALUE_KIND_UNSUPPORTED,
                       0,
                       operation,
                       "ONNX Runtime returned a null composite child.")
                 : dort_status_from_ort(
                       value->runtime,
                       ort_status,
                       DORT_ERROR_VALUE_KIND_UNSUPPORTED,
                       operation);
    }
    status = dort_value_wrap_owned(value->runtime, ort_child, &child);
    if (status != NULL) {
      api->ReleaseValue(ort_child);
      return status;
    }
    status = dort_validate_value_recursive(
        child,
        current_depth + 1u,
        inout_nodes,
        &child_depth,
        operation);
    dort_value_release(child);
    if (status != NULL) {
      return status;
    }
    if (child_depth > maximum_child_depth) {
      maximum_child_depth = child_depth;
    }
  }
  *out_tree_depth = maximum_child_depth + 1u;
  ((dort_value_t*)value)->nesting_depth = *out_tree_depth;
  return NULL;
}

dort_status_t* dort_value_validate_supported(
    const dort_value_t* value,
    const char* operation) {
  size_t nodes = 0u;
  uint32_t tree_depth = 0u;
  return dort_validate_value_recursive(
      value, 1u, &nodes, &tree_depth, operation);
}

dort_status_t* DORT_CALL dort_tensor_info_json(
    const dort_value_t* value,
    dort_string_t* out_json) {
  ONNXTensorElementDataType element_type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  int64_t dimensions[DORT_MAX_TENSOR_RANK];
  size_t dimension_count = 0u;
  size_t byte_length = 0u;
  size_t offset = 0u;
  size_t index = 0u;
  char json[2048];
  dort_status_t* status = NULL;
  int written = 0;

  if (out_json == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_info",
        "The output tensor-info string pointer is null.");
  }
  memset(out_json, 0, sizeof(*out_json));
  out_json->struct_size = (uint32_t)sizeof(*out_json);
  if (!dort_value_is_valid(value)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_info",
        "The tensor value handle is null or invalid.");
  }
  status = dort_query_tensor_element_type(
      value, &element_type, "tensor_info");
  if (status == NULL &&
      element_type == ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING) {
    size_t string_count = 0u;
    status = dort_query_string_tensor(
        value,
        &string_count,
        &byte_length,
        dimensions,
        &dimension_count,
        "tensor_info");
    (void)string_count;
  } else if (status == NULL) {
    status = dort_query_fixed_tensor(
        value,
        &element_type,
        dimensions,
        &dimension_count,
        &byte_length,
        "tensor_info");
  }
  if (status != NULL) {
    return status;
  }
  written = snprintf(
      json,
      sizeof(json),
      "{\"schemaVersion\":1,\"kind\":\"tensor\",\"elementType\":%u,\"dimensions\":[",
      (unsigned int)element_type);
  if (written < 0 || (size_t)written >= sizeof(json)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "tensor_info",
        "The tensor metadata exceeds the ABI limit.");
  }
  offset = (size_t)written;
  for (index = 0u; index < dimension_count; ++index) {
    written = snprintf(
        json + offset,
        sizeof(json) - offset,
        "%s%" PRId64,
        index == 0u ? "" : ",",
        dimensions[index]);
    if (written < 0 || (size_t)written >= sizeof(json) - offset) {
      return dort_status_create(
          DORT_ERROR_DOMAIN_SHIM,
          DORT_ERROR_LIMIT_EXCEEDED,
          0,
          "tensor_info",
          "The tensor metadata exceeds the ABI limit.");
    }
    offset += (size_t)written;
  }
  written = snprintf(
      json + offset,
      sizeof(json) - offset,
      "],\"byteLength\":%zu}",
      byte_length);
  if (written < 0 || (size_t)written >= sizeof(json) - offset) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "tensor_info",
        "The tensor metadata exceeds the ABI limit.");
  }
  return dort_string_copy(json, out_json);
}

dort_status_t* DORT_CALL dort_tensor_copy_data(
    const dort_value_t* value,
    void* destination,
    size_t destination_capacity,
    size_t* out_required_bytes) {
  const OrtApi* api = NULL;
  const void* source = NULL;
  size_t required = 0u;
  ONNXTensorElementDataType element_type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  int64_t dimensions[DORT_MAX_TENSOR_RANK];
  size_t dimension_count = 0u;
  size_t index = 0u;
  dort_status_t* status = NULL;
  OrtStatus* ort_status = NULL;
  if (out_required_bytes == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_copy_data",
        "The required-byte output pointer is null.");
  }
  *out_required_bytes = 0u;
  if (!dort_value_is_valid(value)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_copy_data",
        "The tensor value handle is null or invalid.");
  }
  if (destination == NULL && destination_capacity != 0u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_copy_data",
        "A non-zero destination capacity requires a destination pointer.");
  }
  api = dort_runtime_api(value->runtime);
  status = dort_query_fixed_tensor(
      value,
      &element_type,
      dimensions,
      &dimension_count,
      &required,
      "tensor_copy_data");
  if (status != NULL) {
    return status;
  }
  *out_required_bytes = required;
  if (required > DORT_MAX_TENSOR_BYTES) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "tensor_copy_data",
        "The output tensor exceeds the ABI byte limit.");
  }
  if (destination == NULL && destination_capacity == 0u) {
    return NULL;
  }
  if (destination_capacity < required) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_BUFFER_TOO_SMALL,
        0,
        "tensor_copy_data",
        "The destination buffer is smaller than the tensor data.");
  }
  status = dort_require_cpu_tensor_memory(value, "tensor_copy_data");
  if (status != NULL) {
    return status;
  }
  ort_status = api->GetTensorData(value->value, &source);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        value->runtime, ort_status, DORT_ERROR_NOT_TENSOR, "tensor_copy_data");
  }
  if (required > 0u && source == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_TENSOR_INVALID,
        0,
        "tensor_copy_data",
        "ONNX Runtime returned null storage for a non-empty tensor.");
  }
  if (element_type == ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL) {
    const uint8_t* bool_bytes = (const uint8_t*)source;
    for (index = 0u; index < required; ++index) {
      if (bool_bytes[index] > 1u) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_ORT_API,
            DORT_ERROR_TENSOR_INVALID,
            0,
            "tensor_copy_data",
            "ONNX Runtime returned a Boolean tensor outside the zero-or-one domain.");
      }
    }
  }
  if (required > 0u) {
    memcpy(destination, source, required);
  }
  return NULL;
}

dort_status_t* DORT_CALL dort_tensor_string_count(
    const dort_value_t* value,
    size_t* out_count) {
  size_t total_bytes = 0u;
  if (out_count == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_string_count",
        "The output string-count pointer is null.");
  }
  *out_count = 0u;
  return dort_query_string_tensor(
      value, out_count, &total_bytes, NULL, NULL, "tensor_string_count");
}

dort_status_t* DORT_CALL dort_tensor_string_get(
    const dort_value_t* value,
    size_t index,
    dort_string_t* out_string) {
  const OrtApi* api = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  size_t string_count = 0u;
  size_t total_bytes = 0u;
  size_t string_length = 0u;
  size_t allocation_length = 0u;
  uint8_t* copy = NULL;
  int validation = DORT_ERROR_NONE;
  if (out_string == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_string_get",
        "The output string pointer is null.");
  }
  memset(out_string, 0, sizeof(*out_string));
  out_string->struct_size = (uint32_t)sizeof(*out_string);
  status = dort_query_string_tensor(
      value,
      &string_count,
      &total_bytes,
      NULL,
      NULL,
      "tensor_string_get");
  if (status != NULL) {
    return status;
  }
  if (index >= string_count) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "tensor_string_get",
        "The string tensor index is outside the element range.");
  }
  api = dort_runtime_api(value->runtime);
  ort_status = api->GetStringTensorElementLength(
      value->value, index, &string_length);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        value->runtime,
        ort_status,
        DORT_ERROR_TENSOR_INVALID,
        "tensor_string_get");
  }
  if (string_length > DORT_MAX_STRING_ELEMENT_BYTES ||
      string_length > total_bytes ||
      !dort_checked_add_size(string_length, 1u, &allocation_length)) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_LIMIT_EXCEEDED,
        0,
        "tensor_string_get",
        "The string tensor element exceeds the owned-string limit.");
  }
  copy = (uint8_t*)malloc(allocation_length);
  if (copy == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "tensor_string_get",
        "Could not allocate the copied string tensor element.");
  }
  ort_status = api->GetStringTensorElement(
      value->value, string_length, index, copy);
  if (ort_status != NULL) {
    free(copy);
    return dort_status_from_ort(
        value->runtime,
        ort_status,
        DORT_ERROR_TENSOR_INVALID,
        "tensor_string_get");
  }
  copy[string_length] = 0u;
  validation = dort_validate_utf8_bytes(copy, string_length, 1);
  if (validation != DORT_ERROR_NONE) {
    free(copy);
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        validation,
        0,
        "tensor_string_get",
        "ONNX Runtime returned invalid UTF-8 or an embedded NUL in a string tensor element.");
  }
  out_string->data = copy;
  out_string->length = string_length;
  out_string->private_owner = copy;
  return NULL;
}

dort_status_t* DORT_CALL dort_tensor_data_acquire(
    const dort_value_t* value,
    dort_data_lease_t** out_lease,
    const void** out_data,
    size_t* out_byte_length) {
  const OrtApi* api = NULL;
  const void* data = NULL;
  OrtStatus* ort_status = NULL;
  dort_status_t* status = NULL;
  dort_data_lease_t* lease = NULL;
  ONNXTensorElementDataType element_type =
      ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  int64_t dimensions[DORT_MAX_TENSOR_RANK];
  size_t dimension_count = 0u;
  size_t byte_length = 0u;
  size_t element_width = 0u;
  if (out_lease != NULL) {
    *out_lease = NULL;
  }
  if (out_data != NULL) {
    *out_data = NULL;
  }
  if (out_byte_length != NULL) {
    *out_byte_length = 0u;
  }
  if (out_lease == NULL || out_data == NULL || out_byte_length == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_SHIM,
        DORT_ERROR_INVALID_ARGUMENT,
        0,
        "tensor_data_acquire",
        "All tensor data-acquisition output pointers are required.");
  }
  if (!dort_value_is_valid(value) ||
      value->kind != DORT_VALUE_KIND_TENSOR || value->value == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_DATA_LEASE_UNSUPPORTED,
        0,
        "tensor_data_acquire",
        "A data lease requires a concrete dense tensor value.");
  }
  status = dort_query_tensor_element_type(
      value, &element_type, "tensor_data_acquire");
  if (status != NULL) {
    return status;
  }
  element_width = dort_element_width((uint32_t)element_type);
  if (element_type == ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING ||
      element_width == 0u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_UNSUPPORTED,
        DORT_ERROR_DATA_LEASE_UNSUPPORTED,
        0,
        "tensor_data_acquire",
        "String and unsupported tensor storage cannot be exposed through a host data lease.");
  }
  status = dort_query_fixed_tensor(
      value,
      &element_type,
      dimensions,
      &dimension_count,
      &byte_length,
      "tensor_data_acquire");
  if (status != NULL) {
    return status;
  }
  status = dort_require_cpu_tensor_memory(value, "tensor_data_acquire");
  if (status != NULL) {
    return status;
  }
  api = dort_runtime_api(value->runtime);
  ort_status = api->GetTensorData(value->value, &data);
  if (ort_status != NULL) {
    return dort_status_from_ort(
        value->runtime,
        ort_status,
        DORT_ERROR_DATA_LEASE_UNSUPPORTED,
        "tensor_data_acquire");
  }
  if (byte_length > 0u && data == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_TENSOR_INVALID,
        0,
        "tensor_data_acquire",
        "ONNX Runtime returned null host storage for a non-empty tensor.");
  }
  if (data != NULL && ((uintptr_t)data % element_width) != 0u) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ORT_API,
        DORT_ERROR_DATA_LEASE_UNSUPPORTED,
        0,
        "tensor_data_acquire",
        "ONNX Runtime returned host storage that is misaligned for the tensor element width.");
  }
  if (element_type == ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL) {
    size_t index = 0u;
    for (index = 0u; index < byte_length; ++index) {
      if (((const uint8_t*)data)[index] > 1u) {
        return dort_status_create(
            DORT_ERROR_DOMAIN_ORT_API,
            DORT_ERROR_TENSOR_INVALID,
            0,
            "tensor_data_acquire",
            "ONNX Runtime returned a Boolean tensor outside the zero-or-one domain.");
      }
    }
  }
  lease = (dort_data_lease_t*)calloc(1u, sizeof(*lease));
  if (lease == NULL) {
    return dort_status_create(
        DORT_ERROR_DOMAIN_ALLOCATION,
        DORT_ERROR_ALLOCATION_FAILED,
        0,
        "tensor_data_acquire",
        "Could not allocate the tensor data lease.");
  }
  dort_value_retain((dort_value_t*)value);
  lease->magic = DORT_DATA_LEASE_MAGIC;
  atomic_init(&lease->reference_count, 1u);
  lease->owner_kind = DORT_LEASE_OWNER_VALUE;
  lease->owner.value = (dort_value_t*)value;
  *out_lease = lease;
  *out_data = data;
  *out_byte_length = byte_length;
  return NULL;
}
