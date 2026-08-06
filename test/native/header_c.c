#include "dort.h"

#include <stdint.h>

#if UINTPTR_MAX == UINT64_MAX
_Static_assert(sizeof(dort_runtime_config_t) == 72u, "unexpected 64-bit config size");
_Static_assert(offsetof(dort_runtime_config_t, log_id_utf8) == 32u, "unexpected log ID offset");
_Static_assert(offsetof(dort_runtime_config_t, allowed_root_utf8) == 64u, "unexpected allowed-root offset");
_Static_assert(sizeof(dort_string_pair_t) == 24u, "unexpected pair size");
_Static_assert(sizeof(dort_provider_config_t) == 32u, "unexpected provider size");
_Static_assert(sizeof(dort_session_config_t) == 128u, "unexpected session config size");
_Static_assert(offsetof(dort_session_config_t, log_id_utf8) == 56u, "unexpected session log ID offset");
_Static_assert(offsetof(dort_session_config_t, config_entry_count) == 120u, "unexpected config count offset");
_Static_assert(sizeof(dort_named_value_t) == 24u, "unexpected named value size");
_Static_assert(sizeof(dort_utf8_span_t) == 24u, "unexpected UTF-8 span size");
_Static_assert(offsetof(dort_utf8_span_t, data) == 8u, "unexpected UTF-8 span data offset");
_Static_assert(offsetof(dort_utf8_span_t, length) == 16u, "unexpected UTF-8 span length offset");
_Static_assert(sizeof(dort_external_data_t) == 40u, "unexpected external-data size");
_Static_assert(offsetof(dort_external_data_t, relative_name_utf8) == 8u, "unexpected external-data name offset");
_Static_assert(offsetof(dort_external_data_t, data) == 24u, "unexpected external-data bytes offset");
#elif UINTPTR_MAX == UINT32_MAX
_Static_assert(sizeof(dort_runtime_config_t) == 48u, "unexpected 32-bit config size");
_Static_assert(offsetof(dort_runtime_config_t, log_id_utf8) == 28u, "unexpected log ID offset");
_Static_assert(offsetof(dort_runtime_config_t, allowed_root_utf8) == 44u, "unexpected allowed-root offset");
_Static_assert(sizeof(dort_string_pair_t) == 16u, "unexpected pair size");
_Static_assert(sizeof(dort_provider_config_t) == 20u, "unexpected provider size");
_Static_assert(sizeof(dort_session_config_t) == 88u, "unexpected session config size");
_Static_assert(offsetof(dort_session_config_t, log_id_utf8) == 52u, "unexpected session log ID offset");
_Static_assert(offsetof(dort_session_config_t, config_entry_count) == 84u, "unexpected config count offset");
_Static_assert(sizeof(dort_named_value_t) == 16u, "unexpected named value size");
_Static_assert(sizeof(dort_utf8_span_t) == 16u, "unexpected 32-bit UTF-8 span size");
_Static_assert(offsetof(dort_utf8_span_t, data) == 8u, "unexpected 32-bit UTF-8 span data offset");
_Static_assert(offsetof(dort_utf8_span_t, length) == 12u, "unexpected 32-bit UTF-8 span length offset");
_Static_assert(sizeof(dort_external_data_t) == 24u, "unexpected 32-bit external-data size");
_Static_assert(offsetof(dort_external_data_t, relative_name_utf8) == 8u, "unexpected 32-bit external-data name offset");
_Static_assert(offsetof(dort_external_data_t, data) == 16u, "unexpected 32-bit external-data bytes offset");
#else
#error "Unsupported pointer width"
#endif

int main(void) {
  dort_runtime_config_t config = {0};
  config.struct_size = DORT_RUNTIME_CONFIG_V1_SIZE;
  return DORT_ABI_VERSION == 1u && config.struct_size == sizeof(config) &&
                 DORT_VALUE_KIND_TENSOR == 1 && DORT_VALUE_KIND_OPTIONAL == 6 &&
                 DORT_TENSOR_STRING == 8
             ? 0
             : 1;
}
