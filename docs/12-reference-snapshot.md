# 12. Reference Snapshot and Upstream Sources

Snapshot date: **2026-08-06**

This file records facts used to design the initial implementation. It is not a permanent compatibility guarantee. Before each release, re-check the current official sources and the exact artifacts selected by the application.

## 12.1 ONNX Runtime release/API

Observed:

- GitHub marks ONNX Runtime `v1.28.0` as the latest release on the snapshot date.
- The `v1.28.0` C header defines `ORT_API_VERSION 28`.
- The `v1.27.1` C header defines `ORT_API_VERSION 27`.
- `OrtApiBase::GetVersionString()` returns the runtime version string.
- `OrtApiBase::GetApi(version)` returns null when the requested C API is unsupported, including when a runtime is older than the header/API requested.

Official sources:

- [ONNX Runtime v1.28.0 release](https://github.com/microsoft/onnxruntime/releases/tag/v1.28.0)
- [ONNX Runtime C API `OrtApiBase`](https://onnxruntime.ai/docs/api/c/struct_ort_api_base.html)
- [ONNX Runtime C getting started](https://onnxruntime.ai/docs/get-started/with-c.html)
- [v1.28.0 `onnxruntime_c_api.h`](https://github.com/microsoft/onnxruntime/blob/v1.28.0/include/onnxruntime/core/session/onnxruntime_c_api.h)
- [v1.27.1 `onnxruntime_c_api.h`](https://github.com/microsoft/onnxruntime/blob/v1.27.1/include/onnxruntime/core/session/onnxruntime_c_api.h)

Implementation consequence:

- Use C API 27 as the base shim floor for current sherpa coexistence.
- Runtime-query and negotiate rather than comparing version strings alone.
- Keep API-28-only features outside the API-27 function-table contract.

## 12.2 Execution-provider behavior

Observed:

- ORT partitions graphs through EP capability and supports ordered providers.
- A build containing multiple EPs also needs their dependent libraries available.
- ORT mobile guidance recommends CPU for a first quantized-model baseline and XNNPACK for a first non-quantized baseline, then device/model-specific testing of hardware EPs.
- Partial partitioning can reduce performance.
- The current provider summary includes, among others, AMD MIGraphX, native WebGPU, Vitis AI, and marks the older AMD ROCm EP deprecated.
- In ORT `v1.28.0`, generic provider registration recognizes build-gated names including DML, QNN, OpenVINO, XNNPACK, WebGPU, VitisAI, CoreML, NvTensorRtRtx, MIGraphX, and CPU; CUDA/classic TensorRT and other EPs may still use provider-specific adapters.
- ORT guidance recommends plugin EPs for new provider integrations from ORT 1.23 onward. The built-in TensorRT RTX EP is deprecated in favor of its standalone EP ABI plugin.

Official sources:

- [Execution Providers overview](https://onnxruntime.ai/docs/execution-providers/)
- [Build with different EPs](https://onnxruntime.ai/docs/build/eps.html)
- [Deploy on mobile](https://onnxruntime.ai/docs/tutorials/mobile/)
- [Model usability checker](https://onnxruntime.ai/docs/tutorials/mobile/helpers/model-usability-checker.html)
- [ORT v1.28.0 generic provider registration source](https://github.com/microsoft/onnxruntime/blob/v1.28.0/onnxruntime/core/session/provider_registration.cc)
- [Add a new execution provider / plugin-first guidance](https://onnxruntime.ai/docs/execution-providers/add-execution-provider.html)
- [TensorRT RTX EP](https://onnxruntime.ai/docs/execution-providers/TensorRTRTX-ExecutionProvider.html)
- [MIGraphX EP](https://onnxruntime.ai/docs/execution-providers/MIGraphX-ExecutionProvider.html)
- [Native WebGPU EP](https://onnxruntime.ai/docs/execution-providers/WebGPU-ExecutionProvider.html)
- [Vitis AI EP](https://onnxruntime.ai/docs/execution-providers/Vitis-AI-ExecutionProvider.html)

## 12.3 XNNPACK and CoreML

Observed:

- XNNPACK is documented for Android/iOS and can be built for Windows/Linux.
- Official Android/iOS ORT packages include XNNPACK-capable variants.
- CoreML supports iOS/macOS, with OS requirements depending on CoreML/model-format options.
- CoreML exposes compute-unit, static-shape, model-format, profiling, and cache options.

Official sources:

- [XNNPACK EP](https://onnxruntime.ai/docs/execution-providers/Xnnpack-ExecutionProvider.html)
- [CoreML EP](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html)

Implementation consequence:

- Treat XNNPACK as a mobile floating-point baseline candidate, not a universal win.
- Make CoreML model format/compute units/cache explicit and qualify physical-device assignment.

## 12.4 Android QNN and NNAPI

Observed:

- QNN supports Qualcomm Snapdragon targets on Android and Windows.
- The official Android QNN build path uses an Android ORT source build with QNN integrated and the selected QAIRT/QNN SDK.
- QNN 2.x also exists as a separately registered plugin EP for its documented package targets; this must not be generalized to Android without an actual qualified Android plugin artifact.
- Android NNAPI was deprecated in Android 15, and Android recommends alternatives for performance-critical future workloads.

Official sources:

- [QNN EP](https://onnxruntime.ai/docs/execution-providers/QNN-ExecutionProvider.html)
- [Plugin EP library usage](https://onnxruntime.ai/docs/execution-providers/plugin-ep-libraries/usage.html)
- [QNN plugin EP repository](https://github.com/onnxruntime/onnxruntime-qnn)
- [Build ORT for Android](https://onnxruntime.ai/docs/build/android.html)
- [ORT NNAPI EP](https://onnxruntime.ai/docs/execution-providers/NNAPI-ExecutionProvider.html)
- [Android NNAPI documentation and deprecation warning](https://developer.android.com/ndk/guides/neuralnetworks)

Implementation consequence:

- Android QNN is an aligned custom flavor in the current support contract.
- Desktop/plugin QNN is a separate flavor and lifecycle path.
- NNAPI remains opt-in/legacy and is never the new default.

## 12.5 Windows and desktop providers

Observed:

- DirectML requires sequential execution and disabled memory-pattern optimization.
- ORT builds several provider implementations as shared libraries that must stay next to matching ORT/provider-shared libraries.
- ORT warns against placing provider libraries in global search paths where mismatched ORT versions may load them.

Official sources:

- [DirectML EP](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)
- [OpenVINO EP](https://onnxruntime.ai/docs/execution-providers/OpenVINO-ExecutionProvider.html)
- [Build with different EPs](https://onnxruntime.ai/docs/build/eps.html)

Implementation consequence:

- Enforce DirectML session constraints in typed options.
- Publish heavy providers as application-local, version-aligned flavors.

## 12.6 Dart/Flutter native integration

Observed:

- Dart FFI is the standard C interop mechanism.
- Dart build hooks became available in Dart 3.10; link hooks/native recorded-use tree-shaking are associated with a later Dart SDK.
- Build hooks can compile/download code assets and automatically bundle them.
- Flutter currently recommends the package-FFI/build-hook approach for native bindings when a platform plugin API is not otherwise needed.
- Apple code-asset/library naming must remain consistent across architectures/SDKs.

Official sources:

- [Dart C interop](https://dart.dev/interop/c-interop)
- [Dart hooks](https://dart.dev/tools/hooks)
- [Flutter binding to native code](https://docs.flutter.dev/platform-integration/bind-native-code)
- [Dart NativeFinalizer API](https://api.dart.dev/dart-ffi/NativeFinalizer-class.html)

Implementation consequence:

- Use build hooks as the primary path, with linked/framework integration on iOS.
- Keep explicit disposal despite native finalizers.

## 12.7 Android native packaging

Observed:

- Native libraries in AAR dependencies can collide by identical filename.
- Android's middleware guidance warns that only one identically named library reaches the APK and that older tooling could choose arbitrarily.
- Android recommends a small, explicit native ABI surface and careful STL/native dependency packaging.
- Android 15 supports 16 KB page-size devices; applications with NDK libraries must rebuild compatible native dependencies.

Official sources:

- [Advice for middleware vendors](https://developer.android.com/ndk/guides/middleware-vendors)
- [Native dependencies with AGP](https://developer.android.com/build/native-dependencies)
- [Link Gradle to native libraries](https://developer.android.com/studio/projects/gradle-external-native-builds)
- [Android 15 behavior changes: 16 KB page sizes](https://developer.android.com/about/versions/15/behavior-changes-all)

Implementation consequence:

- No Gradle `pickFirst` for ORT.
- Audit final merged native libraries and ELF metadata.
- Test every transitive library on a 16 KB environment.

## 12.8 sherpa-onnx

Observed from the sherpa-onnx `master` Android arm64 build script on the snapshot date:

- shared build is the default;
- shared mode uses `libsherpa-onnx-jni.so` plus `libonnxruntime.so`;
- static mode links `libonnxruntime.a` into sherpa JNI;
- default Android ORT version variable is `1.27.0`;
- external ORT include/library directories are supported;
- QNN and RKNN build toggles exist.

The sherpa CMake integration also supports preinstalled/external ORT paths and imports a shared `libonnxruntime.so` on Android.

The semantic release `v1.13.4` (2026-07-07) states that sherpa-onnx updated ONNX Runtime to `1.27.0`. GitHub currently marks a later platform-package release named `xcframework` (2026-07-31) as `Latest`; its Apple SPM note sets ONNX Runtime to `1.27.1`. Consequently, the word "latest" must be resolved against the exact Android AAR/source revision or Apple package being consumed, not inferred from the repository's release badge alone.

Official sources:

- [sherpa-onnx repository](https://github.com/k2-fsa/sherpa-onnx)
- [Android arm64 build script](https://github.com/k2-fsa/sherpa-onnx/blob/master/build-android-arm64-v8a.sh)
- [sherpa ONNX Runtime CMake integration](https://github.com/k2-fsa/sherpa-onnx/blob/master/cmake/onnxruntime.cmake)
- [sherpa-onnx v1.13.4 release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.4)
- [sherpa-onnx releases, including the current `xcframework` package release](https://github.com/k2-fsa/sherpa-onnx/releases)

Implementation consequence:

- Default coexistence is sherpa-owned shared ORT plus a runtime-resolved Dart shim.
- Advanced/custom EP coexistence rebuilds sherpa against one aligned ORT.
- CI records exact sherpa revision/AAR hash and per-ABI ORT hash.

## 12.9 Release-update checklist

Before changing any pinned runtime/provider/sherpa version:

1. Check the current official ORT release and release notes.
2. Read C header `ORT_API_VERSION` at the proposed runtime and compatibility-floor tags.
3. Review security fixes and provider/platform breaking changes.
4. Inspect selected ORT artifacts and dependency layout.
5. Inspect current sherpa Android build scripts and exact application AAR.
6. Determine shared vs static sherpa ORT and per-ABI hashes.
7. Review XNNPACK/CoreML/QNN/OpenVINO/CUDA/TensorRT/DirectML/MIGraphX/WebGPU/Vitis AI compatibility sources for affected flavors.
8. Review current Dart/Flutter build-hook and minimum SDK requirements.
9. Review current Android NDK/AGP/16 KB requirements.
10. Update lockfile and this snapshot date.
11. Rebuild and run the full affected platform/provider/sherpa matrix.
12. Publish compatibility impact; never silently replace the ORT binary.

## 12.10 Known uncertainty policy

Where upstream artifacts or documentation differ by platform/release:

- record the exact artifact/revision inspected;
- inspect binary contents and runtime version;
- treat source defaults as evidence about source builds, not proof of a published AAR's content;
- mark untested provider/platform combinations unsupported;
- prefer a controlled error over heuristic compatibility.
