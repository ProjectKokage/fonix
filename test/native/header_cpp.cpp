#include "dort.h"

#include <type_traits>

int main() {
  static_assert(std::is_standard_layout_v<dort_runtime_config_t>);
  static_assert(std::is_standard_layout_v<dort_session_config_t>);
  static_assert(std::is_standard_layout_v<dort_provider_config_t>);
  static_assert(std::is_standard_layout_v<dort_string_pair_t>);
  static_assert(std::is_standard_layout_v<dort_named_value_t>);
  static_assert(std::is_standard_layout_v<dort_utf8_span_t>);
  static_assert(std::is_standard_layout_v<dort_external_data_t>);
  static_assert(DORT_UTF8_SPAN_V1_SIZE == sizeof(dort_utf8_span_t));
  static_assert(DORT_EXTERNAL_DATA_V1_SIZE == sizeof(dort_external_data_t));
  static_assert(DORT_ABI_VERSION == 1u);
  return DORT_RUNTIME_CONFIG_V1_SIZE == sizeof(dort_runtime_config_t) ? 0 : 1;
}
