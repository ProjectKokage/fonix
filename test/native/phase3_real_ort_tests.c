#define _POSIX_C_SOURCE 200809L

#include "dort.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(condition, message)                                               \
  do {                                                                          \
    if (!(condition)) {                                                         \
      fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, (message));    \
      return 1;                                                                 \
    }                                                                           \
  } while (0)

static int check_ok(dort_status_t* status, const char* context) {
  if (status == NULL) {
    return 0;
  }
  fprintf(
      stderr,
      "FAIL %s: domain=%u code=%d ort=%d operation=%s message=%s\n",
      context,
      dort_status_domain(status),
      dort_status_code(status),
      dort_status_ort_code(status),
      dort_status_operation(status),
      dort_status_message(status));
  dort_status_release(status);
  return 1;
}

static int expect_code(dort_status_t* status, int32_t expected) {
  CHECK(status != NULL, "expected an error status");
  CHECK(dort_status_code(status) == expected, "unexpected error code");
  dort_status_release(status);
  return 0;
}

static dort_runtime_config_t runtime_config(
    const char* library,
    const char* root) {
  dort_runtime_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_RUNTIME_CONFIG_V1_SIZE;
  config.shim_abi_version = DORT_ABI_VERSION;
  config.required_ort_api_version = DORT_ORT_API_COMPATIBILITY_FLOOR;
  config.source_kind = DORT_RUNTIME_SOURCE_FILE;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "phase3-real-runtime";
  config.library_path_utf8 = library;
  config.allowed_root_utf8 = root;
  return config;
}

static dort_session_config_t session_config(
    const dort_provider_config_t* provider) {
  dort_session_config_t config;
  memset(&config, 0, sizeof(config));
  config.struct_size = DORT_SESSION_CONFIG_V1_SIZE;
  config.graph_optimization_level = DORT_GRAPH_OPTIMIZATION_ALL;
  config.execution_mode = DORT_EXECUTION_SEQUENTIAL;
  config.enable_cpu_memory_arena = 1u;
  config.enable_memory_pattern = 1u;
  config.deterministic_compute = 1u;
  config.log_severity = DORT_LOG_WARNING;
  config.log_id_utf8 = "phase3-real-session";
  config.providers = provider;
  config.provider_count = 1u;
  return config;
}

static int fixture_path(
    const char* root,
    const char* relative,
    char* destination,
    size_t capacity) {
  int written = snprintf(destination, capacity, "%s/%s", root, relative);
  CHECK(
      written > 0 && (size_t)written < capacity,
      "fixture path exceeds test buffer");
  return 0;
}

static int read_file(
    const char* path,
    uint8_t** out_bytes,
    size_t* out_length) {
  FILE* file = fopen(path, "rb");
  long signed_length = 0;
  uint8_t* bytes = NULL;
  *out_bytes = NULL;
  *out_length = 0u;
  CHECK(file != NULL, "could not open Phase 3 fixture");
  CHECK(fseek(file, 0, SEEK_END) == 0, "could not seek Phase 3 fixture");
  signed_length = ftell(file);
  CHECK(signed_length > 0, "Phase 3 fixture is empty");
  CHECK(fseek(file, 0, SEEK_SET) == 0, "could not rewind Phase 3 fixture");
  bytes = (uint8_t*)malloc((size_t)signed_length);
  CHECK(bytes != NULL, "could not allocate Phase 3 fixture bytes");
  CHECK(
      fread(bytes, 1u, (size_t)signed_length, file) ==
          (size_t)signed_length,
      "could not read Phase 3 fixture");
  CHECK(fclose(file) == 0, "could not close Phase 3 fixture");
  *out_bytes = bytes;
  *out_length = (size_t)signed_length;
  return 0;
}

static int read_fixture(
    const char* root,
    const char* relative,
    uint8_t** out_bytes,
    size_t* out_length) {
  char path[4096];
  CHECK(
      fixture_path(root, relative, path, sizeof(path)) == 0,
      "could not form fixture path");
  return read_file(path, out_bytes, out_length);
}

static int create_fixture_session(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* fixture_root,
    const char* relative,
    dort_session_t** out_session) {
  uint8_t* bytes = NULL;
  size_t length = 0u;
  dort_status_t* status = NULL;
  CHECK(
      read_fixture(fixture_root, relative, &bytes, &length) == 0,
      "fixture read failed");
  status = dort_session_create_from_bytes(
      runtime, options, bytes, length, out_session);
  free(bytes);
  return check_ok(status, relative);
}

static int run_output(
    dort_session_t* session,
    const dort_named_value_t* inputs,
    size_t input_count,
    const char* output_name,
    dort_value_t** out_value) {
  const char* output_names[1];
  dort_run_result_t* result = NULL;
  dort_string_t copied_name;
  dort_status_t* status = NULL;
  output_names[0] = output_name;
  memset(&copied_name, 0, sizeof(copied_name));
  *out_value = NULL;
  status = dort_session_run(
      session,
      NULL,
      inputs,
      input_count,
      output_names,
      1u,
      &result);
  if (check_ok(status, "Phase 3 session run") != 0) {
    return 1;
  }
  CHECK(result != NULL && dort_run_result_count(result) == 1u, "bad run result");
  CHECK(
      check_ok(
          dort_run_result_get(result, 0u, &copied_name, out_value),
          "get Phase 3 output") == 0,
      "could not get Phase 3 output");
  CHECK(
      strcmp((const char*)copied_name.data, output_name) == 0,
      "Phase 3 output name mismatch");
  dort_string_release(&copied_name);
  dort_run_result_release(result);
  return 0;
}

static dort_named_value_t named_value(
    const char* name,
    const dort_value_t* value) {
  dort_named_value_t named;
  memset(&named, 0, sizeof(named));
  named.struct_size = DORT_NAMED_VALUE_V1_SIZE;
  named.name_utf8 = name;
  named.value = value;
  return named;
}

static int tensor_copy_equals(
    const dort_value_t* value,
    const void* expected,
    size_t expected_length) {
  uint8_t* copy = NULL;
  size_t required = 0u;
  CHECK(
      check_ok(
          dort_tensor_copy_data(value, NULL, 0u, &required),
          "query tensor byte count") == 0,
      "tensor byte query failed");
  CHECK(required == expected_length, "tensor byte count mismatch");
  copy = (uint8_t*)malloc(required == 0u ? 1u : required);
  CHECK(copy != NULL, "could not allocate tensor copy");
  CHECK(
      check_ok(
          dort_tensor_copy_data(value, copy, required, &required),
          "copy tensor bytes") == 0,
      "tensor byte copy failed");
  CHECK(memcmp(copy, expected, expected_length) == 0, "tensor bytes mismatch");
  free(copy);
  return 0;
}

static int test_buffer_lease(dort_runtime_t* runtime) {
  dort_buffer_t* buffer = NULL;
  dort_data_lease_t* lease = NULL;
  dort_value_t* tensor = NULL;
  dort_status_t* status = NULL;
  void* data = NULL;
  size_t byte_length = 0u;
  uint8_t source[8] = {1u, 2u, 3u, 4u, 5u, 6u, 7u, 8u};
  uint8_t copy[8] = {0u};
  int64_t shape[1] = {2};
  CHECK(
      check_ok(dort_buffer_allocate(runtime, sizeof(source), 64u, &buffer),
               "allocate lease buffer") == 0,
      "buffer allocation failed");
  CHECK(
      check_ok(dort_buffer_data_acquire(buffer, &lease, &data, &byte_length),
               "acquire buffer lease") == 0,
      "buffer lease failed");
  CHECK(data != NULL && byte_length == sizeof(source), "bad buffer lease data");
  memcpy(data, source, sizeof(source));
  status = dort_buffer_read(buffer, 0u, copy, sizeof(copy));
  CHECK(
      expect_code(status, DORT_ERROR_DATA_LEASE_UNSUPPORTED) == 0,
      "buffer read was not blocked by mutable lease");
  status = dort_tensor_create_with_buffer(
      runtime,
      buffer,
      0u,
      sizeof(source),
      shape,
      1u,
      DORT_TENSOR_FLOAT32,
      &tensor);
  CHECK(tensor == NULL, "tensor attached during mutable buffer lease");
  CHECK(
      expect_code(status, DORT_ERROR_DATA_LEASE_UNSUPPORTED) == 0,
      "buffer lease attachment returned wrong error");
  dort_data_lease_retain(lease);
  dort_data_lease_release(lease);
  status = dort_buffer_write(buffer, 0u, source, sizeof(source));
  CHECK(
      expect_code(status, DORT_ERROR_DATA_LEASE_UNSUPPORTED) == 0,
      "retained mutable lease stopped blocking writes");
  dort_data_lease_release(lease);
  CHECK(
      check_ok(dort_buffer_read(buffer, 0u, copy, sizeof(copy)),
               "read buffer after lease") == 0,
      "buffer stayed locked after final lease release");
  CHECK(memcmp(copy, source, sizeof(copy)) == 0, "leased buffer bytes mismatch");
  dort_buffer_release(buffer);
  return 0;
}

static int test_strings(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* fixture_root) {
  dort_session_t* session = NULL;
  dort_value_t* input = NULL;
  dort_value_t* output = NULL;
  dort_named_value_t named;
  dort_utf8_span_t spans[4];
  dort_utf8_span_t invalid_span;
  dort_status_t* status = NULL;
  dort_string_t string_value;
  dort_string_t tensor_info;
  dort_data_lease_t* lease = NULL;
  const void* leased_data = NULL;
  size_t leased_length = 0u;
  size_t count = 0u;
  size_t index = 0u;
  int64_t shape[1] = {4};
  int64_t invalid_shape[1] = {1};
  uint8_t embedded_nul[3] = {'a', 0u, 'b'};
  uint8_t large[4096];
  const uint8_t ascii[] = "ASCII";
  const uint8_t unicode[] = "こんにちは🌿";
  const uint8_t* expected_data[4];
  size_t expected_lengths[4];
  memset(large, 'x', sizeof(large));
  memset(spans, 0, sizeof(spans));
  for (index = 0u; index < 4u; ++index) {
    spans[index].struct_size = DORT_UTF8_SPAN_V1_SIZE;
  }
  spans[0].data = NULL;
  spans[0].length = 0u;
  spans[1].data = ascii;
  spans[1].length = sizeof(ascii) - 1u;
  spans[2].data = unicode;
  spans[2].length = sizeof(unicode) - 1u;
  spans[3].data = large;
  spans[3].length = sizeof(large);
  expected_data[0] = NULL;
  expected_data[1] = ascii;
  expected_data[2] = unicode;
  expected_data[3] = large;
  expected_lengths[0] = 0u;
  expected_lengths[1] = sizeof(ascii) - 1u;
  expected_lengths[2] = sizeof(unicode) - 1u;
  expected_lengths[3] = sizeof(large);

  memset(&invalid_span, 0, sizeof(invalid_span));
  invalid_span.struct_size = DORT_UTF8_SPAN_V1_SIZE;
  invalid_span.data = embedded_nul;
  invalid_span.length = sizeof(embedded_nul);
  status = dort_tensor_create_strings_copy(
      runtime, &invalid_span, 1u, invalid_shape, 1u, &input);
  CHECK(input == NULL, "embedded-NUL string returned a tensor");
  CHECK(
      expect_code(status, DORT_ERROR_INVALID_UTF8) == 0,
      "embedded-NUL string returned wrong error");

  CHECK(
      check_ok(
          dort_tensor_create_strings_copy(
              runtime, spans, 4u, shape, 1u, &input),
          "create string tensor") == 0,
      "string tensor creation failed");
  CHECK(
      create_fixture_session(
          runtime, options, fixture_root, "string_identity.onnx", &session) == 0,
      "string session creation failed");
  named = named_value("text", input);
  CHECK(run_output(session, &named, 1u, "echo", &output) == 0,
        "string identity run failed");
  CHECK(
      check_ok(dort_tensor_string_count(output, &count), "string count") == 0,
      "string count failed");
  CHECK(count == 4u, "string tensor count mismatch");
  for (index = 0u; index < count; ++index) {
    memset(&string_value, 0, sizeof(string_value));
    CHECK(
        check_ok(
            dort_tensor_string_get(output, index, &string_value),
            "get string element") == 0,
        "string element copy failed");
    CHECK(string_value.length == expected_lengths[index], "string length mismatch");
    if (string_value.length > 0u) {
      CHECK(
          memcmp(string_value.data, expected_data[index], string_value.length) ==
              0,
          "string content mismatch");
    }
    dort_string_release(&string_value);
  }
  memset(&tensor_info, 0, sizeof(tensor_info));
  CHECK(
      check_ok(dort_tensor_info_json(output, &tensor_info), "string tensor info") ==
          0,
      "string tensor info failed");
  CHECK(
      strcmp(
          (const char*)tensor_info.data,
          "{\"schemaVersion\":1,\"kind\":\"tensor\",\"elementType\":8,\"dimensions\":[4],\"byteLength\":4120}") == 0,
      "string tensor info mismatch");
  dort_string_release(&tensor_info);
  status = dort_tensor_data_acquire(
      output, &lease, &leased_data, &leased_length);
  CHECK(lease == NULL && leased_data == NULL && leased_length == 0u,
        "string tensor exposed a host data lease");
  CHECK(
      expect_code(status, DORT_ERROR_DATA_LEASE_UNSUPPORTED) == 0,
      "string tensor lease returned wrong error");
  dort_value_release(output);
  dort_value_release(input);
  dort_session_release(session);
  return 0;
}

static int test_metadata(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* fixture_root) {
  dort_session_t* session = NULL;
  dort_string_t flat;
  dort_string_t recursive;
  dort_string_t model;
  memset(&flat, 0, sizeof(flat));
  memset(&recursive, 0, sizeof(recursive));
  memset(&model, 0, sizeof(model));
  CHECK(
      create_fixture_session(
          runtime, options, fixture_root, "metadata_identity.onnx", &session) ==
          0,
      "metadata session creation failed");
  CHECK(
      check_ok(dort_session_metadata_json(session, &flat), "flat metadata") == 0,
      "flat metadata failed");
  CHECK(
      check_ok(
          dort_session_type_metadata_json(session, &recursive),
          "recursive metadata") == 0,
      "recursive metadata failed");
  CHECK(
      check_ok(
          dort_session_model_metadata_json(session, &model),
          "model metadata") == 0,
      "model metadata failed");
  dort_session_release(session);
  CHECK(
      strstr(
          (const char*)flat.data,
          "{\"schemaVersion\":2,\"inputs\":[{\"name\":\"入力\",\"kind\":\"tensor\",\"elementType\":1,\"hasShape\":true,\"dimensions\":[null,2],\"symbolicDimensions\":[\"batch\",null]}") != NULL,
      "flat metadata v2 mismatch");
  CHECK(
      strstr(
          (const char*)recursive.data,
          "{\"name\":\"入力\",\"type\":{\"kind\":\"tensor\",\"elementType\":1,\"hasShape\":true,\"dimensions\":[null,2],\"symbolicDimensions\":[\"batch\",null]}}") != NULL,
      "recursive tensor metadata mismatch");
  CHECK(
      strcmp(
          (const char*)model.data,
          "{\"schemaVersion\":1,\"producerName\":\"fonix-fixtures\",\"graphName\":\"メタデータグラフ\",\"domain\":\"dev.fonix.fixtures.metadata\",\"description\":\"Model description with UTF-8: 木陰 and café.\",\"graphDescription\":\"Graph description with UTF-8: 木陰.\",\"version\":42,\"customMetadata\":{\"empty\":\"\",\"purpose\":\"model metadata contract\",\"unicode\":\"こんにちは🌿\"}}") == 0,
      "copied model metadata mismatch");
  dort_string_release(&flat);
  dort_string_release(&recursive);
  dort_string_release(&model);
  return 0;
}

static int test_sequence_and_map(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* fixture_root) {
  dort_session_t* sequence_session = NULL;
  dort_session_t* map_session = NULL;
  dort_session_t* int_map_session = NULL;
  dort_value_t* first = NULL;
  dort_value_t* second = NULL;
  dort_value_t* sequence = NULL;
  dort_value_t* child0 = NULL;
  dort_value_t* child1 = NULL;
  dort_value_t* constructed_sequence = NULL;
  dort_value_t* probabilities = NULL;
  dort_value_t* map_sequence = NULL;
  dort_value_t* map = NULL;
  dort_value_t* keys = NULL;
  dort_value_t* values = NULL;
  dort_value_t* constructed_map = NULL;
  dort_value_t* bad_keys = NULL;
  dort_value_t* short_values = NULL;
  dort_value_t* int_probabilities = NULL;
  dort_value_t* int_map_sequence = NULL;
  dort_value_t* int_map = NULL;
  dort_value_t* int_keys = NULL;
  dort_value_t* int_values = NULL;
  dort_value_t* int_constructed_map = NULL;
  dort_named_value_t sequence_inputs[2];
  dort_named_value_t map_input;
  dort_string_t recursive;
  dort_string_t key_string;
  dort_string_t legacy;
  dort_status_t* status = NULL;
  const dort_value_t* sequence_children[2];
  float first_data[2] = {1.0f, 2.0f};
  float second_data[2] = {3.0f, 4.0f};
  float probabilities_data[2] = {0.25f, 0.75f};
  float int_probabilities_data[2] = {0.125f, 0.875f};
  float short_data[1] = {1.0f};
  int64_t bad_key_data[2] = {10, 20};
  int64_t expected_int_keys[2] = {10, 20};
  int64_t vector_shape[1] = {2};
  int64_t probabilities_shape[2] = {1, 2};
  int64_t bad_key_shape[2] = {1, 2};
  int64_t short_shape[1] = {1};
  size_t count = 0u;
  uint32_t kind = 0u;
  memset(&recursive, 0, sizeof(recursive));
  memset(&key_string, 0, sizeof(key_string));
  memset(&legacy, 0, sizeof(legacy));

  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              first_data,
              sizeof(first_data),
              vector_shape,
              1u,
              DORT_TENSOR_FLOAT32,
              &first),
          "create first sequence input") == 0,
      "first sequence input failed");
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              second_data,
              sizeof(second_data),
              vector_shape,
              1u,
              DORT_TENSOR_FLOAT32,
              &second),
          "create second sequence input") == 0,
      "second sequence input failed");
  CHECK(
      create_fixture_session(
          runtime,
          options,
          fixture_root,
          "sequence_construct.onnx",
          &sequence_session) == 0,
      "sequence session failed");
  sequence_inputs[0] = named_value("first", first);
  sequence_inputs[1] = named_value("second", second);
  CHECK(
      run_output(sequence_session, sequence_inputs, 2u, "values", &sequence) ==
          0,
      "sequence run failed");
  CHECK(check_ok(dort_value_kind(sequence, &kind), "sequence kind") == 0,
        "sequence kind failed");
  CHECK(kind == DORT_VALUE_KIND_SEQUENCE, "sequence output kind mismatch");
  CHECK(
      check_ok(dort_value_child_count(sequence, &count), "sequence count") == 0,
      "sequence count failed");
  CHECK(count == 2u, "sequence child count mismatch");
  CHECK(
      check_ok(dort_value_child_get(sequence, 0u, &child0), "sequence child 0") ==
          0,
      "sequence child 0 failed");
  CHECK(
      check_ok(dort_value_child_get(sequence, 1u, &child1), "sequence child 1") ==
          0,
      "sequence child 1 failed");
  CHECK(tensor_copy_equals(child0, first_data, sizeof(first_data)) == 0,
        "sequence child 0 mismatch");
  CHECK(tensor_copy_equals(child1, second_data, sizeof(second_data)) == 0,
        "sequence child 1 mismatch");
  sequence_children[0] = child0;
  sequence_children[1] = child1;
  CHECK(
      check_ok(
          dort_sequence_create(
              runtime, sequence_children, 2u, &constructed_sequence),
          "construct sequence") == 0,
      "sequence construction failed");
  CHECK(
      check_ok(
          dort_value_child_count(constructed_sequence, &count),
          "constructed sequence count") == 0 &&
          count == 2u,
      "constructed sequence count mismatch");
  status = dort_session_metadata_json(sequence_session, &legacy);
  CHECK(
      expect_code(status, DORT_ERROR_VALUE_KIND_UNSUPPORTED) == 0,
      "legacy metadata accepted a composite model");

  CHECK(
      create_fixture_session(
          runtime, options, fixture_root, "zipmap_string.onnx", &map_session) ==
          0,
      "ZipMap session failed");
  CHECK(
      check_ok(
          dort_session_type_metadata_json(map_session, &recursive),
          "ZipMap recursive metadata") == 0,
      "ZipMap recursive metadata failed");
  CHECK(
      strstr(
          (const char*)recursive.data,
          "\"kind\":\"tensor\",\"elementType\":1,\"hasShape\":false,\"dimensions\":[],\"symbolicDimensions\":[]") != NULL,
      "unknown-rank ZipMap value metadata was conflated with scalar shape");
  dort_string_release(&recursive);
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              probabilities_data,
              sizeof(probabilities_data),
              probabilities_shape,
              2u,
              DORT_TENSOR_FLOAT32,
              &probabilities),
          "create ZipMap probabilities") == 0,
      "ZipMap probabilities failed");
  map_input = named_value("probabilities", probabilities);
  CHECK(
      run_output(map_session, &map_input, 1u, "scores", &map_sequence) == 0,
      "ZipMap run failed");
  CHECK(
      check_ok(dort_value_child_get(map_sequence, 0u, &map), "ZipMap map") == 0,
      "ZipMap map child failed");
  CHECK(
      check_ok(dort_value_child_count(map, &count), "ZipMap map count") == 0 &&
          count == 2u,
      "ZipMap map count mismatch");
  CHECK(check_ok(dort_value_child_get(map, 0u, &keys), "ZipMap keys") == 0,
        "ZipMap keys failed");
  CHECK(check_ok(dort_value_child_get(map, 1u, &values), "ZipMap values") == 0,
        "ZipMap values failed");
  CHECK(
      check_ok(dort_tensor_string_count(keys, &count), "ZipMap key count") == 0 &&
          count == 2u,
      "ZipMap key count mismatch");
  CHECK(
      check_ok(dort_tensor_string_get(keys, 0u, &key_string), "ZipMap key") ==
          0,
      "ZipMap key copy failed");
  CHECK(strcmp((const char*)key_string.data, "cat") == 0,
        "ZipMap first key mismatch");
  dort_string_release(&key_string);
  CHECK(
      tensor_copy_equals(values, probabilities_data, sizeof(probabilities_data)) ==
          0,
      "ZipMap values mismatch");
  CHECK(
      check_ok(dort_map_create(runtime, keys, values, &constructed_map),
               "construct map") == 0,
      "map construction failed");
  CHECK(
      check_ok(
          dort_value_child_count(constructed_map, &count),
          "constructed map count") == 0 &&
          count == 2u,
      "constructed map count mismatch");
  dort_value_release(constructed_map);
  constructed_map = NULL;

  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              bad_key_data,
              sizeof(bad_key_data),
              bad_key_shape,
              2u,
              DORT_TENSOR_INT64,
              &bad_keys),
          "create bad map keys") == 0,
      "bad map key tensor failed");
  status = dort_map_create(runtime, bad_keys, values, &constructed_map);
  CHECK(
      expect_code(status, DORT_ERROR_TENSOR_INVALID) == 0,
      "rank-two map keys were accepted");
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              short_data,
              sizeof(short_data),
              short_shape,
              1u,
              DORT_TENSOR_FLOAT32,
              &short_values),
          "create short map values") == 0,
      "short map value tensor failed");
  status = dort_map_create(runtime, keys, short_values, &constructed_map);
  CHECK(
      expect_code(status, DORT_ERROR_TENSOR_INVALID) == 0,
      "mismatched map value cardinality was accepted");

  CHECK(
      create_fixture_session(
          runtime,
          options,
          fixture_root,
          "zipmap_int64.onnx",
          &int_map_session) == 0,
      "int64 ZipMap session failed");
  CHECK(
      check_ok(
          dort_session_type_metadata_json(int_map_session, &recursive),
          "int64 ZipMap recursive metadata") == 0,
      "int64 ZipMap recursive metadata failed");
  CHECK(
      strstr((const char*)recursive.data, "\"keyElementType\":7") != NULL,
      "int64 ZipMap key metadata mismatch");
  dort_string_release(&recursive);
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              int_probabilities_data,
              sizeof(int_probabilities_data),
              probabilities_shape,
              2u,
              DORT_TENSOR_FLOAT32,
              &int_probabilities),
          "create int64 ZipMap probabilities") == 0,
      "int64 ZipMap probabilities failed");
  map_input = named_value("probabilities", int_probabilities);
  CHECK(
      run_output(
          int_map_session,
          &map_input,
          1u,
          "scores",
          &int_map_sequence) == 0,
      "int64 ZipMap run failed");
  CHECK(
      check_ok(
          dort_value_child_get(int_map_sequence, 0u, &int_map),
          "int64 ZipMap map") == 0,
      "int64 ZipMap map child failed");
  CHECK(
      check_ok(dort_value_child_get(int_map, 0u, &int_keys),
               "int64 ZipMap keys") == 0,
      "int64 ZipMap keys failed");
  CHECK(
      check_ok(dort_value_child_get(int_map, 1u, &int_values),
               "int64 ZipMap values") == 0,
      "int64 ZipMap values failed");
  CHECK(
      tensor_copy_equals(
          int_keys, expected_int_keys, sizeof(expected_int_keys)) == 0,
      "int64 ZipMap keys mismatch");
  CHECK(
      tensor_copy_equals(
          int_values,
          int_probabilities_data,
          sizeof(int_probabilities_data)) == 0,
      "int64 ZipMap values mismatch");
  CHECK(
      check_ok(
          dort_map_create(
              runtime, int_keys, int_values, &int_constructed_map),
          "construct int64 map") == 0,
      "int64 map construction failed");

  dort_value_release(int_constructed_map);
  dort_value_release(int_values);
  dort_value_release(int_keys);
  dort_value_release(int_map);
  dort_value_release(int_map_sequence);
  dort_value_release(int_probabilities);
  dort_session_release(int_map_session);
  dort_value_release(short_values);
  dort_value_release(bad_keys);
  dort_value_release(constructed_map);
  dort_value_release(values);
  dort_value_release(keys);
  dort_value_release(map);
  dort_value_release(map_sequence);
  dort_value_release(probabilities);
  dort_session_release(map_session);
  dort_value_release(constructed_sequence);
  dort_value_release(child1);
  dort_value_release(child0);
  dort_value_release(sequence);
  dort_value_release(second);
  dort_value_release(first);
  dort_session_release(sequence_session);
  return 0;
}

static int test_composite_source_lifetime(dort_runtime_t* runtime) {
  dort_value_t* sequence_first = NULL;
  dort_value_t* sequence_second = NULL;
  dort_value_t* sequence = NULL;
  dort_value_t* sequence_child = NULL;
  dort_value_t* map_keys = NULL;
  dort_value_t* map_values = NULL;
  dort_value_t* map = NULL;
  dort_value_t* map_value_child = NULL;
  const dort_value_t* sequence_sources[2];
  float first_data[2] = {1.0f, 2.0f};
  float second_data[2] = {3.0f, 4.0f};
  int64_t key_data[2] = {10, 20};
  float value_data[2] = {5.0f, 6.0f};
  int64_t shape[1] = {2};

  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              first_data,
              sizeof(first_data),
              shape,
              1u,
              DORT_TENSOR_FLOAT32,
              &sequence_first),
          "create retained sequence source 0") == 0,
      "retained sequence source 0 creation failed");
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              second_data,
              sizeof(second_data),
              shape,
              1u,
              DORT_TENSOR_FLOAT32,
              &sequence_second),
          "create retained sequence source 1") == 0,
      "retained sequence source 1 creation failed");
  sequence_sources[0] = sequence_first;
  sequence_sources[1] = sequence_second;
  CHECK(
      check_ok(
          dort_sequence_create(runtime, sequence_sources, 2u, &sequence),
          "construct retained-source sequence") == 0,
      "retained-source sequence construction failed");

  dort_value_release(sequence_first);
  sequence_first = NULL;
  dort_value_release(sequence_second);
  sequence_second = NULL;
  CHECK(
      check_ok(
          dort_value_child_get(sequence, 1u, &sequence_child),
          "get retained-source sequence child") == 0,
      "retained-source sequence child lookup failed");
  dort_value_release(sequence);
  sequence = NULL;
  CHECK(
      tensor_copy_equals(sequence_child, second_data, sizeof(second_data)) == 0,
      "sequence child did not outlive sources and parent");
  dort_value_release(sequence_child);
  sequence_child = NULL;

  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              key_data,
              sizeof(key_data),
              shape,
              1u,
              DORT_TENSOR_INT64,
              &map_keys),
          "create retained map keys") == 0,
      "retained map key creation failed");
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              value_data,
              sizeof(value_data),
              shape,
              1u,
              DORT_TENSOR_FLOAT32,
              &map_values),
          "create retained map values") == 0,
      "retained map value creation failed");
  CHECK(
      check_ok(
          dort_map_create(runtime, map_keys, map_values, &map),
          "construct retained-source map") == 0,
      "retained-source map construction failed");

  dort_value_release(map_keys);
  map_keys = NULL;
  dort_value_release(map_values);
  map_values = NULL;
  CHECK(
      check_ok(
          dort_value_child_get(map, 1u, &map_value_child),
          "get retained-source map values") == 0,
      "retained-source map value lookup failed");
  dort_value_release(map);
  map = NULL;
  CHECK(
      tensor_copy_equals(map_value_child, value_data, sizeof(value_data)) == 0,
      "map values did not outlive sources and parent");
  dort_value_release(map_value_child);
  return 0;
}

static int bool_scalar(const dort_value_t* value, uint8_t expected) {
  CHECK(tensor_copy_equals(value, &expected, sizeof(expected)) == 0,
        "Boolean scalar bytes mismatch");
  return 0;
}

static int test_optional(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* fixture_root) {
  dort_session_t* some_session = NULL;
  dort_session_t* none_session = NULL;
  dort_session_t* input_session = NULL;
  dort_value_t* tensor = NULL;
  dort_value_t* optional = NULL;
  dort_value_t* contained = NULL;
  dort_value_t* none = NULL;
  dort_value_t* some_wrapper = NULL;
  dort_value_t* has_value = NULL;
  dort_named_value_t named;
  float values[2] = {5.0f, 6.0f};
  int64_t shape[1] = {2};
  size_t count = 0u;
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              values,
              sizeof(values),
              shape,
              1u,
              DORT_TENSOR_FLOAT32,
              &tensor),
          "create optional tensor") == 0,
      "optional tensor creation failed");
  CHECK(
      create_fixture_session(
          runtime, options, fixture_root, "optional_tensor.onnx", &some_session) ==
          0,
      "optional Some session failed");
  named = named_value("value", tensor);
  CHECK(run_output(some_session, &named, 1u, "maybe_value", &optional) == 0,
        "optional Some run failed");
  CHECK(
      check_ok(dort_value_child_count(optional, &count), "optional Some count") ==
          0 &&
          count == 1u,
      "optional Some count mismatch");
  CHECK(
      check_ok(dort_value_child_get(optional, 0u, &contained),
               "optional Some child") == 0,
      "optional Some child failed");
  CHECK(tensor_copy_equals(contained, values, sizeof(values)) == 0,
        "optional Some value mismatch");

  CHECK(
      create_fixture_session(
          runtime,
          options,
          fixture_root,
          "optional_empty_tensor.onnx",
          &none_session) == 0,
      "optional None session failed");
  CHECK(run_output(none_session, NULL, 0u, "maybe_value", &none) == 0,
        "optional None run failed");
  CHECK(
      check_ok(dort_value_child_count(none, &count), "optional None count") ==
          0 &&
          count == 0u,
      "optional None count mismatch");
  dort_value_release(none);
  none = NULL;

  CHECK(
      create_fixture_session(
          runtime,
          options,
          fixture_root,
          "optional_has_element.onnx",
          &input_session) == 0,
      "optional input session failed");
  named = named_value("maybe_value", tensor);
  CHECK(run_output(input_session, &named, 1u, "has_value", &has_value) == 0,
        "present optional input failed");
  CHECK(bool_scalar(has_value, 1u) == 0, "present optional input was false");
  dort_value_release(has_value);
  has_value = NULL;
  CHECK(run_output(input_session, NULL, 0u, "has_value", &has_value) == 0,
        "omitted optional input failed");
  CHECK(bool_scalar(has_value, 0u) == 0, "omitted optional input was true");
  dort_value_release(has_value);
  has_value = NULL;
  CHECK(
      check_ok(dort_optional_none_create(runtime, &none), "create optional None") ==
          0,
      "synthetic optional None failed");
  named = named_value("maybe_value", none);
  CHECK(run_output(input_session, &named, 1u, "has_value", &has_value) == 0,
        "synthetic optional None input failed");
  CHECK(bool_scalar(has_value, 0u) == 0, "synthetic optional None was true");
  dort_value_release(has_value);
  has_value = NULL;
  CHECK(
      check_ok(dort_optional_some_create(tensor, &some_wrapper),
               "create optional Some") == 0,
      "synthetic optional Some failed");
  named = named_value("maybe_value", some_wrapper);
  CHECK(run_output(input_session, &named, 1u, "has_value", &has_value) == 0,
        "synthetic optional Some input failed");
  CHECK(bool_scalar(has_value, 1u) == 0, "synthetic optional Some was false");

  dort_value_release(has_value);
  dort_value_release(some_wrapper);
  dort_value_release(none);
  dort_session_release(input_session);
  dort_value_release(contained);
  dort_value_release(optional);
  dort_session_release(none_session);
  dort_session_release(some_session);
  dort_value_release(tensor);
  return 0;
}

static int test_low_precision_one(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* fixture_root,
    const char* fixture,
    const char* input_name,
    const char* output_name,
    uint32_t element_type,
    const uint16_t* bits) {
  dort_session_t* session = NULL;
  dort_value_t* input = NULL;
  dort_value_t* output = NULL;
  dort_named_value_t named;
  dort_data_lease_t* lease = NULL;
  const void* data = NULL;
  size_t byte_length = 0u;
  int64_t shape[1] = {7};
  CHECK(
      create_fixture_session(
          runtime, options, fixture_root, fixture, &session) == 0,
      "low-precision session failed");
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              bits,
              7u * sizeof(*bits),
              shape,
              1u,
              element_type,
              &input),
          "create low-precision tensor") == 0,
      "low-precision tensor failed");
  named = named_value(input_name, input);
  CHECK(run_output(session, &named, 1u, output_name, &output) == 0,
        "low-precision identity failed");
  CHECK(tensor_copy_equals(output, bits, 7u * sizeof(*bits)) == 0,
        "low-precision bits changed");
  CHECK(
      check_ok(
          dort_tensor_data_acquire(output, &lease, &data, &byte_length),
          "acquire low-precision tensor lease") == 0,
      "low-precision lease failed");
  CHECK(
      data != NULL && byte_length == 7u * sizeof(*bits) &&
          ((uintptr_t)data % sizeof(*bits)) == 0u,
      "low-precision lease pointer or alignment mismatch");
  dort_value_release(output);
  output = NULL;
  CHECK(memcmp(data, bits, byte_length) == 0,
        "tensor lease did not retain its exact value owner");
  dort_data_lease_retain(lease);
  dort_data_lease_release(lease);
  CHECK(memcmp(data, bits, byte_length) == 0,
        "retained tensor lease lost its data owner");
  dort_data_lease_release(lease);
  dort_value_release(input);
  dort_session_release(session);
  return 0;
}

static int test_low_precision(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* fixture_root) {
  const uint16_t float16_bits[7] = {
      0x0000u, 0x8000u, 0x3c00u, 0x0001u, 0x0400u, 0x7c00u, 0x7e00u};
  const uint16_t bfloat16_bits[7] = {
      0x0000u, 0x8000u, 0x3f80u, 0x0001u, 0x0080u, 0x7f80u, 0x7fc0u};
  CHECK(
      test_low_precision_one(
          runtime,
          options,
          fixture_root,
          "float16_identity.onnx",
          "float16_input",
          "float16_output",
          DORT_TENSOR_FLOAT16,
          float16_bits) == 0,
      "float16 test failed");
  CHECK(
      test_low_precision_one(
          runtime,
          options,
          fixture_root,
          "bfloat16_identity.onnx",
          "bfloat16_input",
          "bfloat16_output",
          DORT_TENSOR_BFLOAT16,
          bfloat16_bits) == 0,
      "bfloat16 test failed");
  return 0;
}

static int test_external_data(
    dort_runtime_t* runtime,
    const dort_session_options_t* options,
    const char* fixture_root) {
  uint8_t* model = NULL;
  uint8_t* weights = NULL;
  uint8_t* escape_model = NULL;
  uint8_t* outside = NULL;
  uint8_t* missing_model = NULL;
  size_t model_length = 0u;
  size_t weights_length = 0u;
  size_t escape_model_length = 0u;
  size_t outside_length = 0u;
  size_t missing_model_length = 0u;
  dort_external_data_t entry;
  dort_external_data_t duplicates[2];
  dort_session_t* session = NULL;
  dort_value_t* input = NULL;
  dort_value_t* output = NULL;
  dort_named_value_t named;
  dort_status_t* status = NULL;
  const uint8_t safe_name[] = "weights.bin";
  const uint8_t escape_name[] = "../outside.bin";
  const uint8_t colon_name[] = "dir/name:stream";
  const uint8_t control_name[] = {'b', 'a', 'd', '\n'};
  float input_data[4] = {10.0f, 10.0f, 10.0f, 10.0f};
  float expected[4] = {10.0f, 20.0f, 30.0f, 40.0f};
  int64_t shape[1] = {4};
  uint8_t sentinel = 0u;
  CHECK(
      read_fixture(
          fixture_root,
          "external_data/valid/model.onnx",
          &model,
          &model_length) == 0,
      "external model read failed");
  CHECK(
      read_fixture(
          fixture_root,
          "external_data/valid/weights.bin",
          &weights,
          &weights_length) == 0,
      "external weights read failed");
  memset(&entry, 0, sizeof(entry));
  entry.struct_size = DORT_EXTERNAL_DATA_V1_SIZE;
  entry.relative_name_utf8 = safe_name;
  entry.relative_name_length = sizeof(safe_name) - 1u;
  entry.data = weights;
  entry.data_length = weights_length;
  CHECK(
      check_ok(
          dort_session_create_from_bytes_with_external_data(
              runtime,
              options,
              model,
              model_length,
              &entry,
              1u,
              &session),
          "create enumerated external-data session") == 0,
      "external-data session failed");
  memset(model, 0xa5, model_length);
  memset(weights, 0xa5, weights_length);
  free(model);
  model = NULL;
  free(weights);
  weights = NULL;
  CHECK(
      check_ok(
          dort_tensor_create_copy(
              runtime,
              input_data,
              sizeof(input_data),
              shape,
              1u,
              DORT_TENSOR_FLOAT32,
              &input),
          "create external-data input") == 0,
      "external-data input failed");
  named = named_value("input", input);
  CHECK(run_output(session, &named, 1u, "output", &output) == 0,
        "external-data run failed after caller bytes were destroyed");
  CHECK(tensor_copy_equals(output, expected, sizeof(expected)) == 0,
        "external-data numeric output mismatch");
  dort_value_release(output);
  dort_value_release(input);
  dort_session_release(session);
  session = NULL;

  CHECK(
      read_fixture(
          fixture_root,
          "external_data/escape/model.onnx",
          &escape_model,
          &escape_model_length) == 0,
      "escape model read failed");
  CHECK(
      read_fixture(
          fixture_root,
          "external_data/outside.bin",
          &outside,
          &outside_length) == 0,
      "outside sidecar read failed");
  entry.relative_name_utf8 = escape_name;
  entry.relative_name_length = sizeof(escape_name) - 1u;
  entry.data = outside;
  entry.data_length = outside_length;
  status = dort_session_create_from_bytes_with_external_data(
      runtime,
      options,
      escape_model,
      escape_model_length,
      &entry,
      1u,
      &session);
  CHECK(session == NULL, "escaping external-data name returned a session");
  CHECK(
      expect_code(status, DORT_ERROR_EXTERNAL_DATA_INVALID) == 0,
      "escaping external-data name returned wrong error");
  entry.relative_name_utf8 = colon_name;
  entry.relative_name_length = sizeof(colon_name) - 1u;
  status = dort_session_create_from_bytes_with_external_data(
      runtime,
      options,
      escape_model,
      escape_model_length,
      &entry,
      1u,
      &session);
  CHECK(
      expect_code(status, DORT_ERROR_EXTERNAL_DATA_INVALID) == 0,
      "colon external-data name was accepted");
  entry.relative_name_utf8 = control_name;
  entry.relative_name_length = sizeof(control_name);
  status = dort_session_create_from_bytes_with_external_data(
      runtime,
      options,
      escape_model,
      escape_model_length,
      &entry,
      1u,
      &session);
  CHECK(
      expect_code(status, DORT_ERROR_EXTERNAL_DATA_INVALID) == 0,
      "control-byte external-data name was accepted");
  free(escape_model);
  free(outside);

  CHECK(
      read_fixture(
          fixture_root,
          "external_data/missing/model.onnx",
          &missing_model,
          &missing_model_length) == 0,
      "missing model read failed");
  entry.relative_name_utf8 = safe_name;
  entry.relative_name_length = sizeof(safe_name) - 1u;
  entry.data = &sentinel;
  entry.data_length = 1u;
  status = dort_session_create_from_bytes_with_external_data(
      runtime,
      options,
      missing_model,
      missing_model_length,
      &entry,
      1u,
      &session);
  CHECK(session == NULL, "unmatched external-data entry returned a session");
  CHECK(
      expect_code(status, DORT_ERROR_EXTERNAL_DATA_INVALID) == 0,
      "unmatched external-data entry returned wrong error");
  duplicates[0] = entry;
  duplicates[1] = entry;
  status = dort_session_create_from_bytes_with_external_data(
      runtime,
      options,
      missing_model,
      missing_model_length,
      duplicates,
      2u,
      &session);
  CHECK(
      expect_code(status, DORT_ERROR_EXTERNAL_DATA_INVALID) == 0,
      "duplicate external-data entries were accepted");
  entry.data_length = SIZE_MAX;
  status = dort_session_create_from_bytes_with_external_data(
      runtime,
      options,
      missing_model,
      missing_model_length,
      &entry,
      1u,
      &session);
  CHECK(
      expect_code(status, DORT_ERROR_LIMIT_EXCEEDED) == 0,
      "external-data size overflow was not rejected");
  free(missing_model);
  return 0;
}

int main(int argc, char** argv) {
  dort_runtime_t* runtime = NULL;
  dort_session_options_t* options = NULL;
  dort_runtime_config_t native_runtime_config;
  dort_provider_config_t provider;
  dort_session_config_t native_session_config;
  CHECK(argc == 4, "expected ORT library, ORT root, and fixture root");
  memset(&provider, 0, sizeof(provider));
  provider.struct_size = DORT_PROVIDER_CONFIG_V1_SIZE;
  provider.provider_id_utf8 = "cpu";
  native_runtime_config = runtime_config(argv[1], argv[2]);
  native_session_config = session_config(&provider);
  CHECK(
      check_ok(dort_runtime_open(&native_runtime_config, &runtime),
               "open Phase 3 runtime") == 0,
      "Phase 3 runtime failed");
  CHECK(
      check_ok(
          dort_session_options_create(runtime, &native_session_config, &options),
          "create Phase 3 session options") == 0,
      "Phase 3 session options failed");
  CHECK(test_buffer_lease(runtime) == 0, "buffer lease test failed");
  CHECK(test_strings(runtime, options, argv[3]) == 0, "string test failed");
  CHECK(test_metadata(runtime, options, argv[3]) == 0, "metadata test failed");
  CHECK(
      test_sequence_and_map(runtime, options, argv[3]) == 0,
      "sequence/map test failed");
  CHECK(
      test_composite_source_lifetime(runtime) == 0,
      "composite source lifetime test failed");
  CHECK(test_optional(runtime, options, argv[3]) == 0, "optional test failed");
  CHECK(
      test_low_precision(runtime, options, argv[3]) == 0,
      "low-precision test failed");
  CHECK(
      test_external_data(runtime, options, argv[3]) == 0,
      "external-data test failed");
  dort_session_options_release(options);
  dort_runtime_release(runtime);
  dort_data_lease_retain(NULL);
  dort_data_lease_release(NULL);
  printf("Fonix Phase 3 real ORT CPU tests passed.\n");
  return 0;
}
