#define _POSIX_C_SOURCE 200809L

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "onnxruntime_c_api.h"

typedef const OrtApiBase* (*ort_get_api_base_fn)(void);

static int status_ok(const OrtApi* api, OrtStatus* status, const char* operation) {
  if (status == NULL) {
    return 1;
  }
  fprintf(stderr, "%s failed: %s\n", operation, api->GetErrorMessage(status));
  api->ReleaseStatus(status);
  return 0;
}

static int join_path(char* output, size_t output_size, const char* root, const char* relative) {
  const int written = snprintf(output, output_size, "%s/%s", root, relative);
  if (written < 0 || (size_t)written >= output_size) {
    fprintf(stderr, "fixture path is too long: %s\n", relative);
    return 0;
  }
  return 1;
}

static int open_session(const OrtApi* api,
                        const OrtEnv* env,
                        const OrtSessionOptions* options,
                        const char* root,
                        const char* relative,
                        OrtSession** output) {
  char path[4096];
  *output = NULL;
  if (!join_path(path, sizeof(path), root, relative)) {
    return 0;
  }
  return status_ok(api, api->CreateSession(env, path, options, output), relative);
}

static int type_is_tensor(const OrtApi* api,
                          const OrtTypeInfo* type_info,
                          ONNXTensorElementDataType expected_element_type,
                          int expected_has_shape,
                          const int64_t* expected_dimensions,
                          const char* const* expected_symbols,
                          size_t expected_rank) {
  ONNXType kind = ONNX_TYPE_UNKNOWN;
  const OrtTensorTypeAndShapeInfo* tensor_info = NULL;
  ONNXTensorElementDataType element_type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  int64_t dimensions[32];
  const char* symbols[32];
  size_t rank = 0;
  int has_shape = 0;
  if (expected_rank > 32) {
    fprintf(stderr, "validator expected rank is out of bounds: %zu\n", expected_rank);
    return 0;
  }
  if (!status_ok(api, api->GetOnnxTypeFromTypeInfo(type_info, &kind), "GetOnnxTypeFromTypeInfo") ||
      kind != ONNX_TYPE_TENSOR ||
      !status_ok(api, api->CastTypeInfoToTensorInfo(type_info, &tensor_info),
                 "CastTypeInfoToTensorInfo") ||
      tensor_info == NULL ||
      !status_ok(api, api->GetTensorElementType(tensor_info, &element_type),
                 "GetTensorElementType") ||
      !status_ok(api, api->GetDimensionsCount(tensor_info, &rank), "GetDimensionsCount")) {
    return 0;
  }
  has_shape = api->TensorTypeAndShape_HasShape(tensor_info) ? 1 : 0;
  if (element_type != expected_element_type || has_shape != expected_has_shape ||
      rank != expected_rank) {
    fprintf(stderr,
            "unexpected tensor metadata: element=%d hasShape=%d rank=%zu; "
            "expected element=%d hasShape=%d rank=%zu\n",
            (int)element_type, has_shape, rank, (int)expected_element_type,
            expected_has_shape, expected_rank);
    return 0;
  }
  if (rank > 0 &&
      (!status_ok(api, api->GetDimensions(tensor_info, dimensions, rank), "GetDimensions") ||
       !status_ok(api, api->GetSymbolicDimensions(tensor_info, symbols, rank),
                  "GetSymbolicDimensions"))) {
    return 0;
  }
  for (size_t index = 0; index < rank; ++index) {
    const char* expected_symbol = expected_symbols == NULL ? NULL : expected_symbols[index];
    const char* actual_symbol =
        symbols[index] == NULL || symbols[index][0] == '\0' ? NULL : symbols[index];
    if (dimensions[index] != expected_dimensions[index] ||
        ((actual_symbol == NULL) != (expected_symbol == NULL)) ||
        (actual_symbol != NULL && strcmp(actual_symbol, expected_symbol) != 0)) {
      fprintf(stderr,
              "unexpected tensor dimension at %zu: value=%lld symbol=%s; "
              "expected value=%lld symbol=%s\n",
              index, (long long)dimensions[index],
              actual_symbol == NULL ? "<null>" : actual_symbol,
              (long long)expected_dimensions[index],
              expected_symbol == NULL ? "<null>" : expected_symbol);
      return 0;
    }
  }
  return 1;
}

static int tensor_has_element_count(const OrtApi* api,
                                    const OrtValue* value,
                                    size_t expected,
                                    const char* operation) {
  OrtTensorTypeAndShapeInfo* info = NULL;
  size_t count = 0;
  int success = 0;
  if (!status_ok(api, api->GetTensorTypeAndShape(value, &info), operation) ||
      !status_ok(api, api->GetTensorShapeElementCount(info, &count), operation)) {
    goto cleanup;
  }
  if (count != expected) {
    fprintf(stderr, "%s element count mismatch: expected %zu, got %zu\n", operation,
            expected, count);
    goto cleanup;
  }
  success = 1;

cleanup:
  api->ReleaseTensorTypeAndShapeInfo(info);
  return success;
}

static int validate_session_names(const OrtApi* api,
                                  OrtSession* session,
                                  OrtAllocator* allocator,
                                  const char* const* expected_inputs,
                                  size_t expected_input_count,
                                  const char* const* expected_outputs,
                                  size_t expected_output_count) {
  size_t input_count = 0;
  size_t output_count = 0;
  char* name = NULL;
  if (!status_ok(api, api->SessionGetInputCount(session, &input_count),
                 "SessionGetInputCount") ||
      !status_ok(api, api->SessionGetOutputCount(session, &output_count),
                 "SessionGetOutputCount") ||
      input_count != expected_input_count || output_count != expected_output_count) {
    fprintf(stderr, "unexpected session I/O counts: inputs=%zu outputs=%zu\n", input_count,
            output_count);
    return 0;
  }
  for (size_t index = 0; index < input_count; ++index) {
    if (!status_ok(api, api->SessionGetInputName(session, index, allocator, &name),
                   "SessionGetInputName") ||
        name == NULL || strcmp(name, expected_inputs[index]) != 0) {
      fprintf(stderr, "unexpected input name at %zu\n", index);
      if (name != NULL) allocator->Free(allocator, name);
      return 0;
    }
    allocator->Free(allocator, name);
    name = NULL;
  }
  for (size_t index = 0; index < output_count; ++index) {
    if (!status_ok(api, api->SessionGetOutputName(session, index, allocator, &name),
                   "SessionGetOutputName") ||
        name == NULL || strcmp(name, expected_outputs[index]) != 0) {
      fprintf(stderr, "unexpected output name at %zu\n", index);
      if (name != NULL) allocator->Free(allocator, name);
      return 0;
    }
    allocator->Free(allocator, name);
    name = NULL;
  }
  return 1;
}

static int validate_tensor_value_info(const OrtApi* api,
                                      OrtSession* session,
                                      int is_input,
                                      size_t index,
                                      ONNXTensorElementDataType element_type,
                                      int has_shape,
                                      const int64_t* dimensions,
                                      const char* const* symbols,
                                      size_t rank) {
  OrtTypeInfo* type_info = NULL;
  int success = 0;
  OrtStatus* status = is_input ? api->SessionGetInputTypeInfo(session, index, &type_info)
                               : api->SessionGetOutputTypeInfo(session, index, &type_info);
  if (!status_ok(api, status,
                 is_input ? "SessionGetInputTypeInfo" : "SessionGetOutputTypeInfo")) {
    goto cleanup;
  }
  success = type_is_tensor(api, type_info, element_type, has_shape, dimensions, symbols, rank);

cleanup:
  api->ReleaseTypeInfo(type_info);
  return success;
}

static int validate_tensor_identity_metadata(const OrtApi* api,
                                             OrtSession* session,
                                             OrtAllocator* allocator,
                                             const char* input_name,
                                             const char* output_name,
                                             ONNXTensorElementDataType element_type,
                                             int has_shape,
                                             const int64_t* dimensions,
                                             const char* const* symbols,
                                             size_t rank) {
  const char* inputs[] = {input_name};
  const char* outputs[] = {output_name};
  return validate_session_names(api, session, allocator, inputs, 1, outputs, 1) &&
         validate_tensor_value_info(api, session, 1, 0, element_type, has_shape, dimensions,
                                    symbols, rank) &&
         validate_tensor_value_info(api, session, 0, 0, element_type, has_shape, dimensions,
                                    symbols, rank);
}

static int validate_sequence_output(const OrtApi* api, OrtSession* session) {
  OrtTypeInfo* output_type = NULL;
  OrtTypeInfo* element_type = NULL;
  const OrtSequenceTypeInfo* sequence_type = NULL;
  ONNXType kind = ONNX_TYPE_UNKNOWN;
  int success = 0;
  if (!status_ok(api, api->SessionGetOutputTypeInfo(session, 0, &output_type),
                 "SessionGetOutputTypeInfo") ||
      !status_ok(api, api->GetOnnxTypeFromTypeInfo(output_type, &kind),
                 "GetOnnxTypeFromTypeInfo") ||
      kind != ONNX_TYPE_SEQUENCE ||
      !status_ok(api, api->CastTypeInfoToSequenceTypeInfo(output_type, &sequence_type),
                 "CastTypeInfoToSequenceTypeInfo") ||
      sequence_type == NULL ||
      !status_ok(api, api->GetSequenceElementType(sequence_type, &element_type),
                 "GetSequenceElementType")) {
    goto cleanup;
  }
  {
    const int64_t dimensions[] = {2};
    success = type_is_tensor(api, element_type, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                             dimensions, NULL, 1);
  }

cleanup:
  api->ReleaseTypeInfo(element_type);
  api->ReleaseTypeInfo(output_type);
  return success;
}

static int validate_optional_type(const OrtApi* api, OrtSession* session, int is_input) {
  OrtTypeInfo* output_type = NULL;
  OrtTypeInfo* element_type = NULL;
  const OrtOptionalTypeInfo* optional_type = NULL;
  ONNXType kind = ONNX_TYPE_UNKNOWN;
  int success = 0;
  OrtStatus* status = is_input ? api->SessionGetInputTypeInfo(session, 0, &output_type)
                               : api->SessionGetOutputTypeInfo(session, 0, &output_type);
  if (!status_ok(api, status,
                 is_input ? "SessionGetInputTypeInfo" : "SessionGetOutputTypeInfo") ||
      !status_ok(api, api->GetOnnxTypeFromTypeInfo(output_type, &kind),
                 "GetOnnxTypeFromTypeInfo") ||
      kind != ONNX_TYPE_OPTIONAL ||
      !status_ok(api, api->CastTypeInfoToOptionalTypeInfo(output_type, &optional_type),
                 "CastTypeInfoToOptionalTypeInfo") ||
      optional_type == NULL ||
      !status_ok(api, api->GetOptionalContainedTypeInfo(optional_type, &element_type),
                 "GetOptionalContainedTypeInfo")) {
    goto cleanup;
  }
  {
    const int64_t dimensions[] = {2};
    success = type_is_tensor(api, element_type, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                             dimensions, NULL, 1);
  }

cleanup:
  api->ReleaseTypeInfo(element_type);
  api->ReleaseTypeInfo(output_type);
  return success;
}

static int validate_zipmap_output(const OrtApi* api,
                                  OrtSession* session,
                                  ONNXTensorElementDataType expected_key_type) {
  OrtTypeInfo* output_type = NULL;
  OrtTypeInfo* map_element_type = NULL;
  OrtTypeInfo* map_value_type = NULL;
  const OrtSequenceTypeInfo* sequence_type = NULL;
  const OrtMapTypeInfo* map_type = NULL;
  ONNXTensorElementDataType key_type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  ONNXType kind = ONNX_TYPE_UNKNOWN;
  int success = 0;
  if (!status_ok(api, api->SessionGetOutputTypeInfo(session, 0, &output_type),
                 "SessionGetOutputTypeInfo") ||
      !status_ok(api, api->GetOnnxTypeFromTypeInfo(output_type, &kind),
                 "GetOnnxTypeFromTypeInfo") ||
      kind != ONNX_TYPE_SEQUENCE ||
      !status_ok(api, api->CastTypeInfoToSequenceTypeInfo(output_type, &sequence_type),
                 "CastTypeInfoToSequenceTypeInfo") ||
      sequence_type == NULL ||
      !status_ok(api, api->GetSequenceElementType(sequence_type, &map_element_type),
                 "GetSequenceElementType") ||
      !status_ok(api, api->GetOnnxTypeFromTypeInfo(map_element_type, &kind),
                 "GetOnnxTypeFromTypeInfo(map)") ||
      kind != ONNX_TYPE_MAP ||
      !status_ok(api, api->CastTypeInfoToMapTypeInfo(map_element_type, &map_type),
                 "CastTypeInfoToMapTypeInfo") ||
      map_type == NULL ||
      !status_ok(api, api->GetMapKeyType(map_type, &key_type), "GetMapKeyType") ||
      key_type != expected_key_type ||
      !status_ok(api, api->GetMapValueType(map_type, &map_value_type), "GetMapValueType")) {
    goto cleanup;
  }
  success = type_is_tensor(api, map_value_type, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 0, NULL,
                           NULL, 0);

cleanup:
  api->ReleaseTypeInfo(map_value_type);
  api->ReleaseTypeInfo(map_element_type);
  api->ReleaseTypeInfo(output_type);
  return success;
}

static int check_owned_string(const OrtApi* api,
                              OrtAllocator* allocator,
                              OrtStatus* status,
                              char* actual,
                              const char* expected,
                              const char* field) {
  if (!status_ok(api, status, field)) {
    return 0;
  }
  if (actual == NULL || strcmp(actual, expected) != 0) {
    fprintf(stderr, "%s mismatch: expected '%s', got '%s'\n", field, expected,
            actual == NULL ? "<null>" : actual);
    if (actual != NULL) {
      allocator->Free(allocator, actual);
    }
    return 0;
  }
  allocator->Free(allocator, actual);
  return 1;
}

static int validate_metadata(const OrtApi* api, OrtSession* session, OrtAllocator* allocator) {
  OrtModelMetadata* metadata = NULL;
  OrtStatus* status = NULL;
  char* value = NULL;
  int64_t version = 0;
  int success = 0;
  if (!status_ok(api, api->SessionGetModelMetadata(session, &metadata),
                 "SessionGetModelMetadata")) {
    goto cleanup;
  }

  value = NULL;
  status = api->ModelMetadataGetProducerName(metadata, allocator, &value);
  if (!check_owned_string(api, allocator, status, value, "fonix-fixtures", "producerName")) {
    goto cleanup;
  }
  value = NULL;
  status = api->ModelMetadataGetGraphName(metadata, allocator, &value);
  if (!check_owned_string(api, allocator, status, value, "メタデータグラフ", "graphName")) {
    goto cleanup;
  }
  value = NULL;
  status = api->ModelMetadataGetDomain(metadata, allocator, &value);
  if (!check_owned_string(api, allocator, status, value, "dev.fonix.fixtures.metadata",
                          "domain")) {
    goto cleanup;
  }
  value = NULL;
  status = api->ModelMetadataGetDescription(metadata, allocator, &value);
  if (!check_owned_string(api, allocator, status, value,
                          "Model description with UTF-8: 木陰 and café.", "description")) {
    goto cleanup;
  }
  value = NULL;
  status = api->ModelMetadataGetGraphDescription(metadata, allocator, &value);
  if (!check_owned_string(api, allocator, status, value,
                          "Graph description with UTF-8: 木陰.", "graphDescription")) {
    goto cleanup;
  }
  if (!status_ok(api, api->ModelMetadataGetVersion(metadata, &version),
                 "ModelMetadataGetVersion") ||
      version != 42) {
    fprintf(stderr, "modelVersion mismatch: %lld\n", (long long)version);
    goto cleanup;
  }

  const char* const keys[] = {"empty", "purpose", "unicode"};
  const char* const expected[] = {"", "model metadata contract", "こんにちは🌿"};
  for (size_t index = 0; index < 3; ++index) {
    value = NULL;
    status = api->ModelMetadataLookupCustomMetadataMap(metadata, allocator, keys[index], &value);
    if (!check_owned_string(api, allocator, status, value, expected[index], keys[index])) {
      goto cleanup;
    }
  }
  success = 1;

cleanup:
  api->ReleaseModelMetadata(metadata);
  return success;
}

static int validate_string_run(const OrtApi* api,
                               OrtSession* session,
                               OrtAllocator* allocator) {
  const int64_t shape[] = {4};
  const char* input_names[] = {"text"};
  const char* output_names[] = {"echo"};
  const char* values[4];
  char* large = NULL;
  OrtValue* input = NULL;
  OrtValue* output = NULL;
  char* content = NULL;
  size_t offsets[4] = {0, 0, 0, 0};
  size_t content_length = 0;
  int success = 0;

  large = (char*)malloc(4097);
  if (large == NULL) {
    fprintf(stderr, "failed to allocate large string case\n");
    goto cleanup;
  }
  memset(large, 'x', 4096);
  large[4096] = '\0';
  values[0] = "";
  values[1] = "ASCII";
  values[2] = "こんにちは🌿";
  values[3] = large;

  if (!status_ok(api,
                 api->CreateTensorAsOrtValue(allocator, shape, 1,
                                             ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING, &input),
                 "CreateTensorAsOrtValue(string)") ||
      !status_ok(api, api->FillStringTensor(input, values, 4), "FillStringTensor") ||
      !status_ok(api, api->Run(session, NULL, input_names, (const OrtValue* const*)&input, 1,
                               output_names, 1, &output),
                 "Run(string_identity)") ||
      !tensor_has_element_count(api, output, 4, "string output element count") ||
      !status_ok(api, api->GetStringTensorDataLength(output, &content_length),
                 "GetStringTensorDataLength")) {
    goto cleanup;
  }

  content = (char*)malloc(content_length == 0 ? 1 : content_length);
  if (content == NULL) {
    fprintf(stderr, "failed to allocate string output buffer\n");
    goto cleanup;
  }
  if (!status_ok(api,
                 api->GetStringTensorContent(output, content, content_length, offsets, 4),
                 "GetStringTensorContent")) {
    goto cleanup;
  }
  for (size_t index = 0; index < 4; ++index) {
    const size_t end = index + 1 < 4 ? offsets[index + 1] : content_length;
    const size_t expected_length = strlen(values[index]);
    if (offsets[index] > end || end - offsets[index] != expected_length ||
        memcmp(content + offsets[index], values[index], expected_length) != 0) {
      fprintf(stderr, "string output mismatch at index %zu\n", index);
      goto cleanup;
    }
  }
  success = 1;

cleanup:
  free(content);
  api->ReleaseValue(output);
  api->ReleaseValue(input);
  free(large);
  return success;
}

static int validate_u16_identity_run(const OrtApi* api,
                                     OrtSession* session,
                                     OrtAllocator* allocator,
                                     ONNXTensorElementDataType element_type,
                                     const char* input_name,
                                     const char* output_name,
                                     const uint16_t* expected,
                                     size_t value_count,
                                     const char* operation) {
  const int64_t shape[] = {(int64_t)value_count};
  const char* input_names[] = {input_name};
  const char* output_names[] = {output_name};
  OrtValue* input = NULL;
  OrtValue* output = NULL;
  uint16_t* input_data = NULL;
  uint16_t* output_data = NULL;
  int success = 0;

  if (!status_ok(api,
                 api->CreateTensorAsOrtValue(allocator, shape, 1, element_type, &input),
                 "CreateTensorAsOrtValue(u16)") ||
      !tensor_has_element_count(api, input, value_count, "u16 input element count") ||
      !status_ok(api, api->GetTensorMutableData(input, (void**)&input_data),
                 "GetTensorMutableData(u16 input)")) {
    goto cleanup;
  }
  memcpy(input_data, expected, value_count * sizeof(uint16_t));
  if (!status_ok(api, api->Run(session, NULL, input_names, (const OrtValue* const*)&input, 1,
                               output_names, 1, &output),
                 operation) ||
      !tensor_has_element_count(api, output, value_count, "u16 output element count") ||
      !status_ok(api, api->GetTensorMutableData(output, (void**)&output_data),
                 "GetTensorMutableData(u16 output)")) {
    goto cleanup;
  }
  for (size_t index = 0; index < value_count; ++index) {
    if (output_data[index] != expected[index]) {
      fprintf(stderr, "%s raw-bit mismatch at %zu: expected 0x%04x, got 0x%04x\n",
              operation, index, (unsigned)expected[index], (unsigned)output_data[index]);
      goto cleanup;
    }
  }
  success = 1;

cleanup:
  api->ReleaseValue(output);
  api->ReleaseValue(input);
  return success;
}

static int tensor_has_shape(const OrtApi* api,
                            const OrtValue* value,
                            const int64_t* expected_dimensions,
                            size_t expected_rank,
                            const char* operation) {
  OrtTensorTypeAndShapeInfo* info = NULL;
  int64_t dimensions[32];
  size_t rank = 0;
  int success = 0;
  if (expected_rank > 32) {
    fprintf(stderr, "%s expected rank is out of bounds: %zu\n", operation, expected_rank);
    return 0;
  }
  if (!status_ok(api, api->GetTensorTypeAndShape(value, &info), operation) ||
      !status_ok(api, api->GetDimensionsCount(info, &rank), operation) ||
      rank != expected_rank) {
    fprintf(stderr, "%s rank mismatch: expected %zu, got %zu\n", operation, expected_rank,
            rank);
    goto cleanup;
  }
  if (rank > 0 &&
      !status_ok(api, api->GetDimensions(info, dimensions, rank), operation)) {
    goto cleanup;
  }
  for (size_t index = 0; index < rank; ++index) {
    if (dimensions[index] != expected_dimensions[index]) {
      fprintf(stderr, "%s shape mismatch at %zu: expected %lld, got %lld\n", operation,
              index, (long long)expected_dimensions[index], (long long)dimensions[index]);
      goto cleanup;
    }
  }
  success = 1;

cleanup:
  api->ReleaseTensorTypeAndShapeInfo(info);
  return success;
}

static int create_tensor_from_bytes(const OrtApi* api,
                                    OrtAllocator* allocator,
                                    const void* values,
                                    size_t byte_length,
                                    const int64_t* shape,
                                    size_t rank,
                                    size_t value_count,
                                    ONNXTensorElementDataType element_type,
                                    const char* operation,
                                    OrtValue** output) {
  void* data = NULL;
  *output = NULL;
  if (!status_ok(api,
                 api->CreateTensorAsOrtValue(allocator, shape, rank, element_type, output),
                 operation) ||
      !tensor_has_element_count(api, *output, value_count, operation) ||
      !tensor_has_shape(api, *output, shape, rank, operation)) {
    api->ReleaseValue(*output);
    *output = NULL;
    return 0;
  }
  if (byte_length == 0) {
    return 1;
  }
  if (values == NULL ||
      !status_ok(api, api->GetTensorMutableData(*output, &data), operation) || data == NULL) {
    fprintf(stderr, "%s did not expose non-empty tensor storage\n", operation);
    api->ReleaseValue(*output);
    *output = NULL;
    return 0;
  }
  memcpy(data, values, byte_length);
  return 1;
}

static int expect_tensor_bytes(const OrtApi* api,
                               OrtValue* value,
                               const void* expected,
                               size_t byte_length,
                               const int64_t* shape,
                               size_t rank,
                               size_t value_count,
                               const char* operation) {
  void* data = NULL;
  if (!tensor_has_element_count(api, value, value_count, operation) ||
      !tensor_has_shape(api, value, shape, rank, operation)) {
    return 0;
  }
  if (byte_length == 0) {
    return 1;
  }
  if (expected == NULL ||
      !status_ok(api, api->GetTensorMutableData(value, &data), operation) || data == NULL) {
    fprintf(stderr, "%s did not expose non-empty tensor storage\n", operation);
    return 0;
  }
  if (memcmp(data, expected, byte_length) != 0) {
    fprintf(stderr, "%s fixed-width bytes mismatch\n", operation);
    return 0;
  }
  return 1;
}

static int validate_fixed_identity_run(const OrtApi* api,
                                       OrtSession* session,
                                       OrtAllocator* allocator,
                                       const char* input_name,
                                       const char* output_name,
                                       ONNXTensorElementDataType element_type,
                                       const int64_t* shape,
                                       size_t rank,
                                       size_t value_count,
                                       const void* input_bytes,
                                       const void* expected_bytes,
                                       size_t byte_length,
                                       const char* operation) {
  const char* input_names[] = {input_name};
  const char* output_names[] = {output_name};
  OrtValue* input = NULL;
  OrtValue* output = NULL;
  int success = 0;
  if (!create_tensor_from_bytes(api, allocator, input_bytes, byte_length, shape, rank,
                                value_count, element_type, operation, &input) ||
      !status_ok(api, api->Run(session, NULL, input_names,
                               (const OrtValue* const*)&input, 1, output_names, 1, &output),
                 operation) ||
      !expect_tensor_bytes(api, output, expected_bytes, byte_length, shape, rank,
                           value_count, operation)) {
    goto cleanup;
  }
  success = 1;

cleanup:
  api->ReleaseValue(output);
  api->ReleaseValue(input);
  return success;
}

static int validate_dynamic_add_case(const OrtApi* api,
                                     OrtSession* session,
                                     OrtAllocator* allocator,
                                     const int64_t* shape,
                                     size_t rank,
                                     const float* lhs_values,
                                     const float* rhs_values,
                                     const float* expected_values,
                                     size_t value_count,
                                     const char* operation) {
  const char* input_names[] = {"lhs.matrix", "rhs/matrix"};
  const char* output_names[] = {"sum.matrix"};
  OrtValue* inputs[2] = {NULL, NULL};
  OrtValue* output = NULL;
  int success = 0;
  if (!create_tensor_from_bytes(api, allocator, lhs_values, value_count * sizeof(float),
                                shape, rank, value_count,
                                ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, operation,
                                &inputs[0]) ||
      !create_tensor_from_bytes(api, allocator, rhs_values, value_count * sizeof(float),
                                shape, rank, value_count,
                                ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, operation,
                                &inputs[1]) ||
      !status_ok(api, api->Run(session, NULL, input_names,
                               (const OrtValue* const*)inputs, 2, output_names, 1, &output),
                 operation) ||
      !expect_tensor_bytes(api, output, expected_values, value_count * sizeof(float), shape,
                           rank, value_count, operation)) {
    goto cleanup;
  }
  success = 1;

cleanup:
  api->ReleaseValue(output);
  api->ReleaseValue(inputs[1]);
  api->ReleaseValue(inputs[0]);
  return success;
}

static int validate_dynamic_add_run(const OrtApi* api,
                                    OrtSession* session,
                                    OrtAllocator* allocator) {
  const int64_t first_shape[] = {2, 3};
  const float first_lhs[] = {1.0f, -2.0f, 3.0f, 4.5f, -5.5f, 6.0f};
  const float first_rhs[] = {10.0f, 20.0f, -30.0f, 0.5f, 5.5f, -6.0f};
  const float first_expected[] = {11.0f, 18.0f, -27.0f, 5.0f, 0.0f, 0.0f};
  const int64_t second_shape[] = {1, 4};
  const float second_lhs[] = {-1.0f, 0.25f, 8.0f, 100.0f};
  const float second_rhs[] = {1.0f, 0.75f, -3.0f, -25.0f};
  const float second_expected[] = {0.0f, 1.0f, 5.0f, 75.0f};
  return validate_dynamic_add_case(api, session, allocator, first_shape, 2, first_lhs,
                                   first_rhs, first_expected, 6, "Run(dynamic add 2x3)") &&
         validate_dynamic_add_case(api, session, allocator, second_shape, 2, second_lhs,
                                   second_rhs, second_expected, 4,
                                   "Run(dynamic add 1x4)");
}

static int validate_dynamic_matmul_case(const OrtApi* api,
                                        OrtSession* session,
                                        OrtAllocator* allocator,
                                        const int64_t* left_shape,
                                        size_t left_count,
                                        const float* left_values,
                                        const int64_t* right_shape,
                                        size_t right_count,
                                        const float* right_values,
                                        const int64_t* output_shape,
                                        size_t output_count,
                                        const float* expected_values,
                                        const char* operation) {
  const char* input_names[] = {"left.matrix", "right/matrix"};
  const char* output_names[] = {"product.matrix"};
  OrtValue* inputs[2] = {NULL, NULL};
  OrtValue* output = NULL;
  int success = 0;
  if (!create_tensor_from_bytes(api, allocator, left_values,
                                left_count * sizeof(float), left_shape, 2, left_count,
                                ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, operation,
                                &inputs[0]) ||
      !create_tensor_from_bytes(api, allocator, right_values,
                                right_count * sizeof(float), right_shape, 2, right_count,
                                ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, operation,
                                &inputs[1]) ||
      !status_ok(api, api->Run(session, NULL, input_names,
                               (const OrtValue* const*)inputs, 2, output_names, 1, &output),
                 operation) ||
      !expect_tensor_bytes(api, output, expected_values, output_count * sizeof(float),
                           output_shape, 2, output_count, operation)) {
    goto cleanup;
  }
  success = 1;

cleanup:
  api->ReleaseValue(output);
  api->ReleaseValue(inputs[1]);
  api->ReleaseValue(inputs[0]);
  return success;
}

static int validate_dynamic_matmul_run(const OrtApi* api,
                                       OrtSession* session,
                                       OrtAllocator* allocator) {
  const int64_t first_left_shape[] = {2, 3};
  const int64_t first_right_shape[] = {3, 2};
  const int64_t first_output_shape[] = {2, 2};
  const float first_left[] = {1.0f, 2.0f, 3.0f, 4.0f, 5.0f, 6.0f};
  const float first_right[] = {7.0f, 8.0f, 9.0f, 10.0f, 11.0f, 12.0f};
  const float first_expected[] = {58.0f, 64.0f, 139.0f, 154.0f};
  const int64_t second_left_shape[] = {1, 2};
  const int64_t second_right_shape[] = {2, 3};
  const int64_t second_output_shape[] = {1, 3};
  const float second_left[] = {-1.0f, 2.0f};
  const float second_right[] = {3.0f, 4.0f, 5.0f, -6.0f, 7.0f, 8.0f};
  const float second_expected[] = {-15.0f, 10.0f, 11.0f};
  return validate_dynamic_matmul_case(
             api, session, allocator, first_left_shape, 6, first_left,
             first_right_shape, 6, first_right, first_output_shape, 4,
             first_expected, "Run(dynamic MatMul 2x3 by 3x2)") &&
         validate_dynamic_matmul_case(
             api, session, allocator, second_left_shape, 2, second_left,
             second_right_shape, 6, second_right, second_output_shape, 3,
             second_expected, "Run(dynamic MatMul 1x2 by 2x3)");
}

static int validate_fixed_width_integer_metadata(const OrtApi* api,
                                                 OrtSession* session,
                                                 OrtAllocator* allocator) {
  const char* const input_names[] = {
      "signed/int8.input",    "unsigned/uint8.input",  "signed/int16.input",
      "unsigned/uint16.input", "signed/int32.input",    "unsigned/uint32.input",
      "signed/int64.input",    "unsigned/uint64.input"};
  const char* const output_names[] = {
      "signed/int8.output",    "unsigned/uint8.output",  "signed/int16.output",
      "unsigned/uint16.output", "signed/int32.output",    "unsigned/uint32.output",
      "signed/int64.output",    "unsigned/uint64.output"};
  const ONNXTensorElementDataType element_types[] = {
      ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8,   ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8,
      ONNX_TENSOR_ELEMENT_DATA_TYPE_INT16,  ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT16,
      ONNX_TENSOR_ELEMENT_DATA_TYPE_INT32,  ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT32,
      ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64,  ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT64};
  const int64_t shape[] = {4};
  if (!validate_session_names(api, session, allocator, input_names, 8, output_names, 8)) {
    return 0;
  }
  for (size_t index = 0; index < 8; ++index) {
    if (!validate_tensor_value_info(api, session, 1, index, element_types[index], 1, shape,
                                    NULL, 1) ||
        !validate_tensor_value_info(api, session, 0, index, element_types[index], 1, shape,
                                    NULL, 1)) {
      return 0;
    }
  }
  return 1;
}

static int validate_fixed_width_integer_run(const OrtApi* api,
                                            OrtSession* session,
                                            OrtAllocator* allocator) {
  const int8_t int8_input[] = {INT8_MIN, -1, 0, INT8_MAX};
  const int8_t int8_expected[] = {INT8_MIN, -1, 0, INT8_MAX};
  const uint8_t uint8_input[] = {0, 1, 254, UINT8_MAX};
  const uint8_t uint8_expected[] = {0, 1, 254, UINT8_MAX};
  const int16_t int16_input[] = {INT16_MIN, -1, 0, INT16_MAX};
  const int16_t int16_expected[] = {INT16_MIN, -1, 0, INT16_MAX};
  const uint16_t uint16_input[] = {0, 1, 65534, UINT16_MAX};
  const uint16_t uint16_expected[] = {0, 1, 65534, UINT16_MAX};
  const int32_t int32_input[] = {INT32_MIN, -1, 0, INT32_MAX};
  const int32_t int32_expected[] = {INT32_MIN, -1, 0, INT32_MAX};
  const uint32_t uint32_input[] = {0u, 1u, UINT32_C(4294967294), UINT32_MAX};
  const uint32_t uint32_expected[] = {0u, 1u, UINT32_C(4294967294), UINT32_MAX};
  const int64_t int64_input[] = {INT64_MIN, -1, 0, INT64_MAX};
  const int64_t int64_expected[] = {INT64_MIN, -1, 0, INT64_MAX};
  const uint64_t uint64_input[] = {
      0u, 1u, UINT64_C(4294967296), UINT64_C(9223372036854775807)};
  const uint64_t uint64_expected[] = {
      0u, 1u, UINT64_C(4294967296), UINT64_C(9223372036854775807)};
  const char* input_names[] = {
      "signed/int8.input",    "unsigned/uint8.input",  "signed/int16.input",
      "unsigned/uint16.input", "signed/int32.input",    "unsigned/uint32.input",
      "signed/int64.input",    "unsigned/uint64.input"};
  const char* output_names[] = {
      "unsigned/uint64.output", "signed/int64.output",    "unsigned/uint32.output",
      "signed/int32.output",    "unsigned/uint16.output", "signed/int16.output",
      "unsigned/uint8.output",  "signed/int8.output"};
  const void* input_data[] = {int8_input,  uint8_input,  int16_input, uint16_input,
                              int32_input, uint32_input, int64_input, uint64_input};
  const size_t input_sizes[] = {sizeof(int8_input),  sizeof(uint8_input),
                                sizeof(int16_input), sizeof(uint16_input),
                                sizeof(int32_input), sizeof(uint32_input),
                                sizeof(int64_input), sizeof(uint64_input)};
  const ONNXTensorElementDataType input_types[] = {
      ONNX_TENSOR_ELEMENT_DATA_TYPE_INT8,   ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT8,
      ONNX_TENSOR_ELEMENT_DATA_TYPE_INT16,  ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT16,
      ONNX_TENSOR_ELEMENT_DATA_TYPE_INT32,  ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT32,
      ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64,  ONNX_TENSOR_ELEMENT_DATA_TYPE_UINT64};
  const void* expected_data[] = {uint64_expected, int64_expected, uint32_expected,
                                 int32_expected,  uint16_expected, int16_expected,
                                 uint8_expected,  int8_expected};
  const size_t expected_sizes[] = {sizeof(uint64_expected), sizeof(int64_expected),
                                   sizeof(uint32_expected), sizeof(int32_expected),
                                   sizeof(uint16_expected), sizeof(int16_expected),
                                   sizeof(uint8_expected), sizeof(int8_expected)};
  const int64_t shape[] = {4};
  OrtValue* inputs[8] = {NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL};
  OrtValue* outputs[8] = {NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL};
  int success = 0;
  for (size_t index = 0; index < 8; ++index) {
    if (!create_tensor_from_bytes(api, allocator, input_data[index], input_sizes[index],
                                  shape, 1, 4, input_types[index],
                                  "Create fixed-width integer input", &inputs[index])) {
      goto cleanup;
    }
  }
  if (!status_ok(api, api->Run(session, NULL, input_names,
                               (const OrtValue* const*)inputs, 8, output_names, 8, outputs),
                 "Run(fixed-width integer identities)")) {
    goto cleanup;
  }
  for (size_t index = 0; index < 8; ++index) {
    if (!expect_tensor_bytes(api, outputs[index], expected_data[index],
                             expected_sizes[index], shape, 1, 4,
                             "fixed-width integer output")) {
      goto cleanup;
    }
  }
  success = 1;

cleanup:
  for (size_t index = 0; index < 8; ++index) {
    api->ReleaseValue(outputs[index]);
    api->ReleaseValue(inputs[index]);
  }
  return success;
}

static int create_float_tensor(const OrtApi* api,
                               OrtAllocator* allocator,
                               const float* values,
                               const int64_t* shape,
                               size_t rank,
                               size_t value_count,
                               OrtValue** output) {
  float* data = NULL;
  *output = NULL;
  if (!status_ok(api,
                 api->CreateTensorAsOrtValue(allocator, shape, rank,
                                             ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, output),
                 "CreateTensorAsOrtValue(float)") ||
      !tensor_has_element_count(api, *output, value_count, "float input element count") ||
      !status_ok(api, api->GetTensorMutableData(*output, (void**)&data),
                 "GetTensorMutableData(float)")) {
    api->ReleaseValue(*output);
    *output = NULL;
    return 0;
  }
  memcpy(data, values, value_count * sizeof(float));
  return 1;
}

static int expect_float_tensor(const OrtApi* api,
                               OrtValue* value,
                               const float* expected,
                               size_t value_count,
                               const char* operation) {
  float* data = NULL;
  if (!tensor_has_element_count(api, value, value_count, operation) ||
      !status_ok(api, api->GetTensorMutableData(value, (void**)&data), operation)) {
    return 0;
  }
  for (size_t index = 0; index < value_count; ++index) {
    if (data[index] != expected[index]) {
      fprintf(stderr, "%s mismatch at %zu: expected %f, got %f\n", operation, index,
              expected[index], data[index]);
      return 0;
    }
  }
  return 1;
}

static int expect_bool_scalar(const OrtApi* api,
                              OrtValue* value,
                              uint8_t expected,
                              const char* operation) {
  uint8_t* data = NULL;
  if (!tensor_has_element_count(api, value, 1, operation) ||
      !status_ok(api, api->GetTensorMutableData(value, (void**)&data), operation)) {
    return 0;
  }
  if (*data != expected) {
    fprintf(stderr, "%s mismatch: expected %u, got %u\n", operation, (unsigned)expected,
            (unsigned)*data);
    return 0;
  }
  return 1;
}

static int validate_unknown_rank_case(const OrtApi* api,
                                      OrtSession* session,
                                      OrtAllocator* allocator,
                                      const int64_t* shape,
                                      size_t rank,
                                      const float* values,
                                      size_t value_count) {
  const char* input_names[] = {"unknown_input"};
  const char* output_names[] = {"unknown_output"};
  OrtValue* input = NULL;
  OrtValue* output = NULL;
  int success = 0;
  if (!create_float_tensor(api, allocator, values, shape, rank, value_count, &input) ||
      !status_ok(api, api->Run(session, NULL, input_names, (const OrtValue* const*)&input, 1,
                               output_names, 1, &output),
                 "Run(unknown-rank identity)") ||
      !expect_float_tensor(api, output, values, value_count, "unknown-rank output")) {
    goto cleanup;
  }
  success = 1;

cleanup:
  api->ReleaseValue(output);
  api->ReleaseValue(input);
  return success;
}

static int validate_unknown_rank_run(const OrtApi* api,
                                     OrtSession* session,
                                     OrtAllocator* allocator) {
  const int64_t rank_one_shape[] = {3};
  const int64_t rank_two_shape[] = {2, 2};
  const float rank_one_values[] = {1.0f, 2.0f, 3.0f};
  const float rank_two_values[] = {4.0f, 5.0f, 6.0f, 7.0f};
  return validate_unknown_rank_case(api, session, allocator, rank_one_shape, 1,
                                    rank_one_values, 3) &&
         validate_unknown_rank_case(api, session, allocator, rank_two_shape, 2,
                                    rank_two_values, 4);
}

static int validate_metadata_identity_run(const OrtApi* api,
                                          OrtSession* session,
                                          OrtAllocator* allocator) {
  const int64_t shape[] = {2, 2};
  const float values[] = {1.0f, 2.0f, 3.0f, 4.0f};
  const char* input_names[] = {"入力"};
  const char* output_names[] = {"出力"};
  OrtValue* input = NULL;
  OrtValue* output = NULL;
  int success = 0;
  if (!create_float_tensor(api, allocator, values, shape, 2, 4, &input) ||
      !status_ok(api, api->Run(session, NULL, input_names, (const OrtValue* const*)&input, 1,
                               output_names, 1, &output),
                 "Run(metadata identity)") ||
      !expect_float_tensor(api, output, values, 4, "metadata identity output")) {
    goto cleanup;
  }
  success = 1;

cleanup:
  api->ReleaseValue(output);
  api->ReleaseValue(input);
  return success;
}

static int validate_sequence_run(const OrtApi* api,
                                 OrtSession* session,
                                 OrtAllocator* allocator) {
  const int64_t shape[] = {2};
  const float first_values[] = {1.0f, 2.0f};
  const float second_values[] = {3.0f, 4.0f};
  const char* input_names[] = {"first", "second"};
  const char* output_names[] = {"values"};
  OrtValue* inputs[2] = {NULL, NULL};
  OrtValue* output = NULL;
  OrtValue* element = NULL;
  ONNXType kind = ONNX_TYPE_UNKNOWN;
  size_t count = 0;
  int success = 0;

  if (!create_float_tensor(api, allocator, first_values, shape, 1, 2, &inputs[0]) ||
      !create_float_tensor(api, allocator, second_values, shape, 1, 2, &inputs[1]) ||
      !status_ok(api, api->Run(session, NULL, input_names, (const OrtValue* const*)inputs, 2,
                               output_names, 1, &output),
                 "Run(sequence_construct)") ||
      !status_ok(api, api->GetValueType(output, &kind), "GetValueType(sequence)") ||
      kind != ONNX_TYPE_SEQUENCE ||
      !status_ok(api, api->GetValueCount(output, &count), "GetValueCount(sequence)") ||
      count != 2) {
    fprintf(stderr, "sequence output kind/count mismatch: kind=%d count=%zu\n", (int)kind,
            count);
    goto cleanup;
  }
  for (int index = 0; index < 2; ++index) {
    const float* expected = index == 0 ? first_values : second_values;
    if (!status_ok(api, api->GetValue(output, index, allocator, &element),
                   "GetValue(sequence element)") ||
        !expect_float_tensor(api, element, expected, 2, "sequence element")) {
      goto cleanup;
    }
    api->ReleaseValue(element);
    element = NULL;
  }
  success = 1;

cleanup:
  api->ReleaseValue(element);
  api->ReleaseValue(output);
  api->ReleaseValue(inputs[1]);
  api->ReleaseValue(inputs[0]);
  return success;
}

static int validate_optional_output_run(const OrtApi* api,
                                        OrtSession* session,
                                        OrtAllocator* allocator,
                                        int expected_present) {
  const int64_t shape[] = {2};
  const float values[] = {5.0f, 6.0f};
  const char* input_names[] = {"value"};
  const char* output_names[] = {"maybe_value"};
  OrtValue* input = NULL;
  OrtValue* output = NULL;
  ONNXType kind = ONNX_TYPE_UNKNOWN;
  int has_value = -1;
  int success = 0;

  if (expected_present &&
      !create_float_tensor(api, allocator, values, shape, 1, 2, &input)) {
    goto cleanup;
  }
  if (!status_ok(api,
                 api->Run(session, NULL, expected_present ? input_names : NULL,
                          expected_present ? (const OrtValue* const*)&input : NULL,
                          expected_present ? 1 : 0, output_names, 1, &output),
                 expected_present ? "Run(optional some)" : "Run(optional none)")) {
    goto cleanup;
  }
  if (!expected_present) {
    if (output == NULL ||
        !status_ok(api, api->HasValue(output, &has_value), "HasValue(optional None)") ||
        has_value != 0) {
      fprintf(stderr, "optional None did not produce a non-null empty OrtValue\n");
      goto cleanup;
    }
    printf("optional output present=0 GetValueType=invalid HasValue=0\n");
    success = 1;
    goto cleanup;
  }
  if (output == NULL ||
      !status_ok(api, api->GetValueType(output, &kind), "GetValueType(optional output)") ||
      !status_ok(api, api->HasValue(output, &has_value), "HasValue(optional output)") ||
      kind != ONNX_TYPE_TENSOR || has_value != 1) {
    fprintf(stderr, "optional output mismatch: kind=%d hasValue=%d expected=%d\n", (int)kind,
            has_value, expected_present);
    goto cleanup;
  }
  printf("optional output present=%d GetValueType=%d HasValue=%d\n", expected_present,
         (int)kind, has_value);
  if (expected_present && !expect_float_tensor(api, output, values, 2, "optional some data")) {
    goto cleanup;
  }
  success = 1;

cleanup:
  api->ReleaseValue(output);
  api->ReleaseValue(input);
  return success;
}

static int validate_optional_input_run(const OrtApi* api,
                                       OrtSession* session,
                                       OrtAllocator* allocator,
                                       int present) {
  const int64_t shape[] = {2};
  const float values[] = {5.0f, 6.0f};
  const char* input_names[] = {"maybe_value"};
  const char* output_names[] = {"has_value"};
  OrtValue* input = NULL;
  OrtValue* output = NULL;
  int success = 0;

  if (present && !create_float_tensor(api, allocator, values, shape, 1, 2, &input)) {
    goto cleanup;
  }
  if (!status_ok(api,
                 api->Run(session, NULL, present ? input_names : NULL,
                          present ? (const OrtValue* const*)&input : NULL, present ? 1 : 0,
                          output_names, 1, &output),
                 present ? "Run(optional input some)" : "Run(optional input omitted)") ||
      !expect_bool_scalar(api, output, present ? 1 : 0, "optional has-element output")) {
    goto cleanup;
  }
  success = 1;

cleanup:
  api->ReleaseValue(output);
  api->ReleaseValue(input);
  return success;
}

static int validate_zipmap_run(const OrtApi* api,
                               OrtSession* session,
                               OrtAllocator* allocator) {
  const int64_t shape[] = {1, 2};
  const float probabilities[] = {0.25f, 0.75f};
  const float expected_scores[] = {0.25f, 0.75f};
  const char* input_names[] = {"probabilities"};
  const char* output_names[] = {"scores"};
  const char* expected_keys[] = {"cat", "dog"};
  OrtValue* input = NULL;
  OrtValue* output = NULL;
  OrtValue* map = NULL;
  OrtValue* keys = NULL;
  OrtValue* values = NULL;
  ONNXType kind = ONNX_TYPE_UNKNOWN;
  size_t count = 0;
  size_t content_length = 0;
  size_t offsets[2] = {0, 0};
  char* content = NULL;
  int success = 0;

  if (!create_float_tensor(api, allocator, probabilities, shape, 2, 2, &input) ||
      !status_ok(api, api->Run(session, NULL, input_names, (const OrtValue* const*)&input, 1,
                               output_names, 1, &output),
                 "Run(zipmap)") ||
      !status_ok(api, api->GetValueType(output, &kind), "GetValueType(zipmap sequence)") ||
      kind != ONNX_TYPE_SEQUENCE ||
      !status_ok(api, api->GetValueCount(output, &count), "GetValueCount(zipmap sequence)") ||
      count != 1 ||
      !status_ok(api, api->GetValue(output, 0, allocator, &map), "GetValue(zipmap map)") ||
      !status_ok(api, api->GetValueType(map, &kind), "GetValueType(zipmap map)") ||
      kind != ONNX_TYPE_MAP ||
      !status_ok(api, api->GetValueCount(map, &count), "GetValueCount(zipmap map)") ||
      count != 2 ||
      !status_ok(api, api->GetValue(map, 0, allocator, &keys), "GetValue(zipmap keys)") ||
      !status_ok(api, api->GetValue(map, 1, allocator, &values), "GetValue(zipmap values)") ||
      !tensor_has_element_count(api, keys, 2, "zipmap string key count") ||
      !status_ok(api, api->GetStringTensorDataLength(keys, &content_length),
                 "GetStringTensorDataLength(zipmap keys)")) {
    fprintf(stderr, "zipmap output kind/count mismatch: kind=%d count=%zu\n", (int)kind,
            count);
    goto cleanup;
  }
  content = (char*)malloc(content_length == 0 ? 1 : content_length);
  if (content == NULL ||
      !status_ok(api, api->GetStringTensorContent(keys, content, content_length, offsets, 2),
                 "GetStringTensorContent(zipmap keys)")) {
    goto cleanup;
  }
  for (size_t index = 0; index < 2; ++index) {
    const size_t end = index + 1 < 2 ? offsets[index + 1] : content_length;
    const size_t expected_length = strlen(expected_keys[index]);
    if (offsets[index] > end || end - offsets[index] != expected_length ||
        memcmp(content + offsets[index], expected_keys[index], expected_length) != 0) {
      fprintf(stderr, "zipmap key mismatch at %zu\n", index);
      goto cleanup;
    }
  }
  if (!expect_float_tensor(api, values, expected_scores, 2, "zipmap values")) {
    goto cleanup;
  }
  success = 1;

cleanup:
  free(content);
  api->ReleaseValue(values);
  api->ReleaseValue(keys);
  api->ReleaseValue(map);
  api->ReleaseValue(output);
  api->ReleaseValue(input);
  return success;
}

static int validate_zipmap_int64_run(const OrtApi* api,
                                     OrtSession* session,
                                     OrtAllocator* allocator) {
  const int64_t shape[] = {1, 2};
  const float probabilities[] = {0.125f, 0.875f};
  const float expected_scores[] = {0.125f, 0.875f};
  const int64_t expected_keys[] = {10, 20};
  const char* input_names[] = {"probabilities"};
  const char* output_names[] = {"scores"};
  OrtValue* input = NULL;
  OrtValue* output = NULL;
  OrtValue* map = NULL;
  OrtValue* keys = NULL;
  OrtValue* values = NULL;
  int64_t* key_data = NULL;
  ONNXType kind = ONNX_TYPE_UNKNOWN;
  size_t count = 0;
  int success = 0;

  if (!create_float_tensor(api, allocator, probabilities, shape, 2, 2, &input) ||
      !status_ok(api, api->Run(session, NULL, input_names, (const OrtValue* const*)&input, 1,
                               output_names, 1, &output),
                 "Run(zipmap int64)") ||
      !status_ok(api, api->GetValueType(output, &kind),
                 "GetValueType(zipmap int64 sequence)") ||
      kind != ONNX_TYPE_SEQUENCE ||
      !status_ok(api, api->GetValueCount(output, &count),
                 "GetValueCount(zipmap int64 sequence)") ||
      count != 1 ||
      !status_ok(api, api->GetValue(output, 0, allocator, &map),
                 "GetValue(zipmap int64 map)") ||
      !status_ok(api, api->GetValueType(map, &kind), "GetValueType(zipmap int64 map)") ||
      kind != ONNX_TYPE_MAP ||
      !status_ok(api, api->GetValueCount(map, &count), "GetValueCount(zipmap int64 map)") ||
      count != 2 ||
      !status_ok(api, api->GetValue(map, 0, allocator, &keys),
                 "GetValue(zipmap int64 keys)") ||
      !status_ok(api, api->GetValue(map, 1, allocator, &values),
                 "GetValue(zipmap int64 values)") ||
      !tensor_has_element_count(api, keys, 2, "zipmap int64 key count") ||
      !status_ok(api, api->GetTensorMutableData(keys, (void**)&key_data),
                 "GetTensorMutableData(zipmap int64 keys)")) {
    fprintf(stderr, "int64 zipmap output kind/count mismatch: kind=%d count=%zu\n", (int)kind,
            count);
    goto cleanup;
  }
  for (size_t index = 0; index < 2; ++index) {
    if (key_data[index] != expected_keys[index]) {
      fprintf(stderr, "int64 zipmap key mismatch at %zu\n", index);
      goto cleanup;
    }
  }
  if (!expect_float_tensor(api, values, expected_scores, 2, "int64 zipmap values")) {
    goto cleanup;
  }
  success = 1;

cleanup:
  api->ReleaseValue(values);
  api->ReleaseValue(keys);
  api->ReleaseValue(map);
  api->ReleaseValue(output);
  api->ReleaseValue(input);
  return success;
}

static int validate_external_run(const OrtApi* api,
                                 OrtSession* session,
                                 OrtAllocator* allocator) {
  const int64_t shape[] = {4};
  const char* input_names[] = {"input"};
  const char* output_names[] = {"output"};
  const float expected[] = {10.0f, 20.0f, 30.0f, 40.0f};
  OrtValue* input = NULL;
  OrtValue* output = NULL;
  float* input_data = NULL;
  float* output_data = NULL;
  int success = 0;

  if (!status_ok(api,
                 api->CreateTensorAsOrtValue(allocator, shape, 1,
                                             ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &input),
                 "CreateTensorAsOrtValue(float)") ||
      !tensor_has_element_count(api, input, 4, "external input element count") ||
      !status_ok(api, api->GetTensorMutableData(input, (void**)&input_data),
                 "GetTensorMutableData(input)")) {
    goto cleanup;
  }
  for (size_t index = 0; index < 4; ++index) {
    input_data[index] = 10.0f;
  }
  if (!status_ok(api, api->Run(session, NULL, input_names, (const OrtValue* const*)&input, 1,
                               output_names, 1, &output),
                 "Run(external_data)") ||
      !tensor_has_element_count(api, output, 4, "external output element count") ||
      !status_ok(api, api->GetTensorMutableData(output, (void**)&output_data),
                 "GetTensorMutableData(output)")) {
    goto cleanup;
  }
  for (size_t index = 0; index < 4; ++index) {
    if (output_data[index] != expected[index]) {
      fprintf(stderr, "external-data output mismatch at %zu: %f\n", index,
              output_data[index]);
      goto cleanup;
    }
  }
  success = 1;

cleanup:
  api->ReleaseValue(output);
  api->ReleaseValue(input);
  return success;
}

static int expect_session_failure(const OrtApi* api,
                                  const OrtEnv* env,
                                  const OrtSessionOptions* options,
                                  const char* root,
                                  const char* relative) {
  char path[4096];
  OrtSession* session = NULL;
  OrtStatus* status = NULL;
  if (!join_path(path, sizeof(path), root, relative)) {
    return 0;
  }
  status = api->CreateSession(env, path, options, &session);
  if (status == NULL) {
    fprintf(stderr, "%s unexpectedly loaded\n", relative);
    api->ReleaseSession(session);
    return 0;
  }
  api->ReleaseStatus(status);
  return 1;
}

static int observe_escape_fixture(const OrtApi* api,
                                  const OrtEnv* env,
                                  const OrtSessionOptions* options,
                                  const char* root) {
  char path[4096];
  OrtSession* session = NULL;
  OrtStatus* status = NULL;
  if (!join_path(path, sizeof(path), root, "external_data/escape/model.onnx")) {
    return 0;
  }
  status = api->CreateSession(env, path, options, &session);
  if (status == NULL) {
    printf("raw ORT accepted ../ external data; wrapper sandbox must reject it\n");
    api->ReleaseSession(session);
  } else {
    printf("raw ORT rejected ../ external data; wrapper must still enforce its own sandbox\n");
    api->ReleaseStatus(status);
  }
  return 1;
}

int main(int argc, char** argv) {
  void* library = NULL;
  ort_get_api_base_fn get_api_base = NULL;
  const OrtApiBase* api_base = NULL;
  const OrtApi* api = NULL;
  OrtEnv* env = NULL;
  OrtSessionOptions* options = NULL;
  OrtAllocator* allocator = NULL;
  OrtSession* session = NULL;
  const uint16_t float16_bits[] = {0x0000, 0x8000, 0x3c00, 0x0001,
                                   0x0400, 0x7c00, 0x7e00};
  const uint16_t bfloat16_bits[] = {0x0000, 0x8000, 0x3f80, 0x0001,
                                    0x0080, 0x7f80, 0x7fc0};
  const double scalar_input = -3.25;
  const double scalar_expected = -3.25;
  const uint8_t bool_input[] = {0, 1, 1, 0, 1, 0};
  const uint8_t bool_expected[] = {0, 1, 1, 0, 1, 0};
  const int64_t shape_2[] = {2};
  const int64_t shape_4[] = {4};
  const int64_t shape_6[] = {6};
  const int64_t shape_7[] = {7};
  const int64_t zero_shape[] = {0, 3};
  const int64_t dynamic_shape_2[] = {-1, 2};
  const char* const dynamic_symbols_2[] = {"batch", NULL};
  const int64_t fully_dynamic_shape[] = {-1, -1};
  const char* const fully_dynamic_symbols[] = {"rows", "columns"};
  const char* const matmul_left_symbols[] = {"rows", "inner"};
  const char* const matmul_right_symbols[] = {"inner", "columns"};
  const char* const matmul_output_symbols[] = {"rows", "columns"};
  int success = 0;

  if (argc < 3 || argc > 4) {
    fprintf(stderr, "usage: %s <libonnxruntime> <fixture-root> [expected-version]\n", argv[0]);
    return 2;
  }
  library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (library == NULL) {
    fprintf(stderr, "dlopen failed: %s\n", dlerror());
    return 1;
  }
  *(void**)(&get_api_base) = dlsym(library, "OrtGetApiBase");
  if (get_api_base == NULL) {
    fprintf(stderr, "OrtGetApiBase is missing\n");
    return 1;
  }
  api_base = get_api_base();
  if (api_base == NULL || api_base->GetApi == NULL || api_base->GetVersionString == NULL) {
    fprintf(stderr, "invalid OrtApiBase\n");
    return 1;
  }
  if (argc == 4 && strcmp(api_base->GetVersionString(), argv[3]) != 0) {
    fprintf(stderr, "expected ORT %s, got %s\n", argv[3], api_base->GetVersionString());
    return 1;
  }
  api = api_base->GetApi(27);
  if (api == NULL) {
    fprintf(stderr, "runtime %s does not support C API 27\n", api_base->GetVersionString());
    return 1;
  }
  if (!status_ok(api, api->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "fonix-fixtures", &env),
                 "CreateEnv") ||
      !status_ok(api, api->CreateSessionOptions(&options), "CreateSessionOptions") ||
      !status_ok(api, api->GetAllocatorWithDefaultOptions(&allocator),
                 "GetAllocatorWithDefaultOptions")) {
    goto cleanup;
  }

  if (!open_session(api, env, options, argv[2], "string_identity.onnx", &session) ||
      !validate_tensor_identity_metadata(api, session, allocator, "text", "echo",
                                         ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING, 1, shape_4,
                                         NULL, 1) ||
      !validate_string_run(api, session, allocator)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "float16_identity.onnx", &session) ||
      !validate_tensor_identity_metadata(api, session, allocator, "float16_input",
                                         "float16_output",
                                         ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT16, 1, shape_7,
                                         NULL, 1) ||
      !validate_u16_identity_run(api, session, allocator,
                                 ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT16, "float16_input",
                                 "float16_output", float16_bits, 7, "Run(float16 identity)")) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "bfloat16_identity.onnx", &session) ||
      !validate_tensor_identity_metadata(api, session, allocator, "bfloat16_input",
                                         "bfloat16_output",
                                         ONNX_TENSOR_ELEMENT_DATA_TYPE_BFLOAT16, 1, shape_7,
                                         NULL, 1) ||
      !validate_u16_identity_run(api, session, allocator,
                                 ONNX_TENSOR_ELEMENT_DATA_TYPE_BFLOAT16, "bfloat16_input",
                                 "bfloat16_output", bfloat16_bits, 7,
                                 "Run(bfloat16 identity)")) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "scalar_float64_identity.onnx", &session) ||
      !validate_tensor_identity_metadata(api, session, allocator, "scalar.input",
                                         "scalar/output",
                                         ONNX_TENSOR_ELEMENT_DATA_TYPE_DOUBLE, 1, NULL,
                                         NULL, 0) ||
      !validate_fixed_identity_run(api, session, allocator, "scalar.input",
                                   "scalar/output", ONNX_TENSOR_ELEMENT_DATA_TYPE_DOUBLE,
                                   NULL, 0, 1, &scalar_input, &scalar_expected,
                                   sizeof(scalar_input), "Run(rank-zero float64 identity)")) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "dynamic_add.onnx", &session) ||
      !validate_session_names(api, session, allocator,
                              (const char* const[]){"lhs.matrix", "rhs/matrix"}, 2,
                              (const char* const[]){"sum.matrix"}, 1) ||
      !validate_tensor_value_info(api, session, 1, 0,
                                  ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  fully_dynamic_shape, fully_dynamic_symbols, 2) ||
      !validate_tensor_value_info(api, session, 1, 1,
                                  ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  fully_dynamic_shape, fully_dynamic_symbols, 2) ||
      !validate_tensor_value_info(api, session, 0, 0,
                                  ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  fully_dynamic_shape, fully_dynamic_symbols, 2) ||
      !validate_dynamic_add_run(api, session, allocator)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "dynamic_matmul.onnx", &session) ||
      !validate_session_names(api, session, allocator,
                              (const char* const[]){"left.matrix", "right/matrix"}, 2,
                              (const char* const[]){"product.matrix"}, 1) ||
      !validate_tensor_value_info(api, session, 1, 0,
                                  ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  fully_dynamic_shape, matmul_left_symbols, 2) ||
      !validate_tensor_value_info(api, session, 1, 1,
                                  ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  fully_dynamic_shape, matmul_right_symbols, 2) ||
      !validate_tensor_value_info(api, session, 0, 0,
                                  ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  fully_dynamic_shape, matmul_output_symbols, 2) ||
      !validate_dynamic_matmul_run(api, session, allocator)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "zero_length_float32_identity.onnx",
                    &session) ||
      !validate_tensor_identity_metadata(api, session, allocator, "empty/input",
                                         "empty.output",
                                         ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                         zero_shape, NULL, 2) ||
      !validate_fixed_identity_run(api, session, allocator, "empty/input", "empty.output",
                                   ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, zero_shape, 2, 0,
                                   NULL, NULL, 0,
                                   "Run(zero-length float32 identity)")) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "bool_identity.onnx", &session) ||
      !validate_tensor_identity_metadata(api, session, allocator, "flags/input",
                                         "flags.normalized",
                                         ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL, 1, shape_6,
                                         NULL, 1) ||
      !validate_fixed_identity_run(api, session, allocator, "flags/input",
                                   "flags.normalized", ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL,
                                   shape_6, 1, 6, bool_input, bool_expected,
                                   sizeof(bool_input), "Run(normalized bool identity)")) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "fixed_width_integer_identities.onnx",
                    &session) ||
      !validate_fixed_width_integer_metadata(api, session, allocator) ||
      !validate_fixed_width_integer_run(api, session, allocator)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "metadata_identity.onnx", &session) ||
      !validate_tensor_identity_metadata(api, session, allocator, "入力", "出力",
                                         ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                         dynamic_shape_2, dynamic_symbols_2, 2) ||
      !validate_metadata(api, session, allocator) ||
      !validate_metadata_identity_run(api, session, allocator)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "unknown_rank_identity.onnx", &session) ||
      !validate_tensor_identity_metadata(api, session, allocator, "unknown_input",
                                         "unknown_output",
                                         ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 0, NULL, NULL,
                                         0) ||
      !validate_unknown_rank_run(api, session, allocator)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "sequence_construct.onnx", &session) ||
      !validate_session_names(api, session, allocator,
                              (const char* const[]){"first", "second"}, 2,
                              (const char* const[]){"values"}, 1) ||
      !validate_tensor_value_info(api, session, 1, 0, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  shape_2, NULL, 1) ||
      !validate_tensor_value_info(api, session, 1, 1, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  shape_2, NULL, 1) ||
      !validate_sequence_output(api, session) ||
      !validate_sequence_run(api, session, allocator)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "optional_tensor.onnx", &session) ||
      !validate_session_names(api, session, allocator, (const char* const[]){"value"}, 1,
                              (const char* const[]){"maybe_value"}, 1) ||
      !validate_tensor_value_info(api, session, 1, 0, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  shape_2, NULL, 1) ||
      !validate_optional_type(api, session, 0) ||
      !validate_optional_output_run(api, session, allocator, 1)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "optional_empty_tensor.onnx", &session) ||
      !validate_session_names(api, session, allocator, NULL, 0,
                              (const char* const[]){"maybe_value"}, 1) ||
      !validate_optional_type(api, session, 0) ||
      !validate_optional_output_run(api, session, allocator, 0)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "optional_has_element.onnx", &session) ||
      !validate_session_names(api, session, allocator,
                              (const char* const[]){"maybe_value"}, 1,
                              (const char* const[]){"has_value"}, 1) ||
      !validate_optional_type(api, session, 1) ||
      !validate_tensor_value_info(api, session, 0, 0, ONNX_TENSOR_ELEMENT_DATA_TYPE_BOOL, 1,
                                  NULL, NULL, 0) ||
      !validate_optional_input_run(api, session, allocator, 1) ||
      !validate_optional_input_run(api, session, allocator, 0)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "zipmap_string.onnx", &session) ||
      !validate_session_names(api, session, allocator,
                              (const char* const[]){"probabilities"}, 1,
                              (const char* const[]){"scores"}, 1) ||
      !validate_tensor_value_info(api, session, 1, 0, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  dynamic_shape_2, dynamic_symbols_2, 2) ||
      !validate_zipmap_output(api, session, ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING) ||
      !validate_zipmap_run(api, session, allocator)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "zipmap_int64.onnx", &session) ||
      !validate_session_names(api, session, allocator,
                              (const char* const[]){"probabilities"}, 1,
                              (const char* const[]){"scores"}, 1) ||
      !validate_tensor_value_info(api, session, 1, 0, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1,
                                  dynamic_shape_2, dynamic_symbols_2, 2) ||
      !validate_zipmap_output(api, session, ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64) ||
      !validate_zipmap_int64_run(api, session, allocator)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!open_session(api, env, options, argv[2], "external_data/valid/model.onnx", &session) ||
      !validate_tensor_identity_metadata(api, session, allocator, "input", "output",
                                         ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, 1, shape_4,
                                         NULL, 1) ||
      !validate_external_run(api, session, allocator)) {
    goto cleanup;
  }
  api->ReleaseSession(session);
  session = NULL;

  if (!expect_session_failure(api, env, options, argv[2],
                              "external_data/missing/model.onnx") ||
      !observe_escape_fixture(api, env, options, argv[2])) {
    goto cleanup;
  }

  printf("validated Phase-3 fixtures with ONNX Runtime %s (C API 27)\n",
         api_base->GetVersionString());
  success = 1;

cleanup:
  api->ReleaseSession(session);
  api->ReleaseSessionOptions(options);
  api->ReleaseEnv(env);
  /* ORT is intentionally left loaded until process exit. */
  return success ? 0 : 1;
}
