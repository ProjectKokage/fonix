#include <stdio.h>

#include "onnxruntime_c_api.h"

int main(void) {
  const OrtApiBase* base = OrtGetApiBase();
  const OrtApi* api = NULL;
  OrtStatus* status = NULL;
  char** providers = NULL;
  int provider_count = 0;
  int index = 0;

  if (base == NULL || base->GetApi == NULL || base->GetVersionString == NULL) {
    fputs("ONNX Runtime returned an incomplete API base.\n", stderr);
    return 2;
  }
  api = base->GetApi(27);
  if (api == NULL || api->GetAvailableProviders == NULL ||
      api->ReleaseAvailableProviders == NULL || api->GetErrorMessage == NULL ||
      api->ReleaseStatus == NULL) {
    fputs("ONNX Runtime API 27 is unavailable or incomplete.\n", stderr);
    return 3;
  }

  printf("runtimeVersion=%s\n", base->GetVersionString());
  status = api->GetAvailableProviders(&providers, &provider_count);
  if (status != NULL) {
    fprintf(stderr, "%s\n", api->GetErrorMessage(status));
    api->ReleaseStatus(status);
    return 4;
  }
  if (provider_count <= 0 || providers == NULL) {
    fputs("ONNX Runtime returned no available providers.\n", stderr);
    return 5;
  }
  for (index = 0; index < provider_count; ++index) {
    if (providers[index] == NULL) {
      fputs("ONNX Runtime returned a null provider name.\n", stderr);
      status = api->ReleaseAvailableProviders(providers, provider_count);
      if (status != NULL) {
        api->ReleaseStatus(status);
      }
      return 6;
    }
    puts(providers[index]);
  }
  status = api->ReleaseAvailableProviders(providers, provider_count);
  if (status != NULL) {
    fprintf(stderr, "%s\n", api->GetErrorMessage(status));
    api->ReleaseStatus(status);
    return 7;
  }
  return 0;
}
