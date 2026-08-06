#include <stdint.h>

#if defined(_WIN32)
__declspec(dllexport)
#else
__attribute__((visibility("default")))
#endif
uint32_t fonix_fake_not_ort(void) {
  return 1u;
}
