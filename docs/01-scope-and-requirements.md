# 1. Scope and Requirements

## 1.1 Product statement

Build a Dart package that provides safe, idiomatic access to ONNX Runtime inference while preserving enough control for production deployment and high-performance execution providers. It must work in Flutter applications and standalone Dart Native applications where the target platform supports Dart FFI and the selected native artifact.

The wrapper is responsible for the Dart API, C shim, native artifact resolution, lifecycle safety, execution-provider configuration, diagnostics, tests, and release metadata. It is not responsible for model conversion, quantization, or model-specific pre/post-processing, although it should expose the primitives needed by higher-level packages.

## 1.2 Required platforms and architecture tiers

### Tier 1: release-gating targets

| Platform | Architectures | Baseline native flavor | Required integration evidence |
|---|---|---|---|
| iOS | device arm64; simulator arm64 | CPU + XNNPACK + CoreML where included | physical device and simulator app |
| macOS | arm64; x86_64 when the selected ORT build supports it | CPU; optional XNNPACK/CoreML | packaged `.app` plus CLI test |
| Android | arm64-v8a, x86_64 | CPU + XNNPACK; sherpa-owned process mode | APK/AAB inspection and device/emulator tests |
| Linux | x86_64, arm64 | CPU | ELF/package test on both architectures |
| Windows | x64, arm64 where runner/hardware is available | CPU | packaged executable and DLL search test |

### Tier 2: qualified flavors

- iOS/macOS CoreML.
- Android QNN on selected Snapdragon hardware.
- Windows DirectML, CUDA/classic TensorRT, QNN, OpenVINO, native WebGPU, TensorRT RTX plugin, and Vitis AI as separately published/tested flavors where the upstream stack supports the target hardware.
- Linux CUDA/classic TensorRT, OpenVINO, oneDNN, XNNPACK, AMD MIGraphX, native WebGPU, TensorRT RTX plugin, and vendor-specific accelerator EPs as separately published/tested flavors.
- Android NNAPI as a legacy compatibility option, not the preferred forward path.
- Android `armeabi-v7a` only if there is a concrete consumer requirement and the full dependency chain remains supportable.

A platform name alone is insufficient. Every public support claim names the operating system, CPU architecture, runtime flavor, minimum deployment target, and validation tier.

## 1.3 Functional requirements

### Runtime and environment

- Open a bundled, process-owned, aligned, or explicitly supplied desktop ONNX Runtime.
- Query the actual runtime version.
- Negotiate the required ONNX Runtime C API version before creating an environment.
- Create an environment with configurable log severity and a caller-supplied log identifier.
- Share the environment safely across sessions created from the same runtime identity.
- Reject attempts to combine handles created by different runtime identities.

### Model and session

- Create sessions from file paths and in-memory bytes.
- Support ONNX models with external data through an explicit, sandboxable base directory or ORT-supported external-initializer configuration.
- Expose model metadata and input/output type information.
- Configure graph optimization, execution mode, intra-op/inter-op thread counts, memory arena, memory pattern, profiling, and deterministic compute where supported.
- Register an ordered execution-provider list with string key/value provider options.
- Offer strict provider requirements and explicit CPU fallback policy.
- Support cancellation/termination through run options where ORT supports it.

### Values and tensors

Before 1.0, support:

- dense tensors for all practical ONNX scalar element types;
- scalar tensors with rank zero;
- dynamic and symbolic dimensions in metadata;
- string tensors with copy-in/copy-out semantics;
- sequences, maps, optional values, and common combinations used by ONNX-ML;
- safe output ownership and optional native-backed views;
- explicit native buffers for zero-copy input and output workflows;
- caller-provided output buffers or I/O binding where supported and justified by benchmarks.

Sparse tensors and opaque/custom value types may be post-1.0 if clearly reported as unsupported and covered by a roadmap issue.

### Inference

- Run by named inputs and requested output names.
- Validate duplicate/missing names and type/shape mismatches before native invocation where metadata permits.
- Provide a synchronous core API.
- Provide a high-level asynchronous API implemented by a dedicated worker isolate that owns its session.
- Support session pooling for throughput without pretending all EPs allow concurrent calls on one session.
- Preserve native error codes, messages, and contextual operation names.

### Diagnostics

- Enumerate runtime/build information and provider state.
- Record provider registration failures.
- Distinguish “compiled into artifact,” “loadable,” “registered,” and “assigned nodes.”
- Make CPU fallback observable.
- Expose ORT profiling output or a normalized summary suitable for automated qualification.

## 1.4 Performance requirements

- The shim adds no avoidable copy beyond the selected safety mode.
- The default API may copy Dart heap data into native memory, but an explicit native-buffer path must eliminate repeated copies for steady-state inference.
- Session creation and model compilation are never hidden inside `run()`.
- Threading defaults are conservative and overrideable; the wrapper does not blindly set thread counts to logical CPU count.
- XNNPACK and ORT CPU thread pools must not be configured to oversubscribe each other.
- Provider initialization caches, such as CoreML model cache, TensorRT engine cache, or QNN context cache, are explicit, versioned, and tied to model/runtime/provider identities.
- Benchmarks report cold initialization separately from warm inference.
- Performance claims include p50/p95/p99 latency, throughput, resident memory, model-load time, and, on mobile, thermal/energy observations.

No EP is enabled by default solely because it is present. The automatic policy may recommend an EP, but release qualification requires model- and device-specific evidence.

## 1.5 Reliability and safety requirements

- All owning resources have deterministic and idempotent disposal.
- Use-after-dispose throws a Dart state error before FFI.
- Parent/child lifetimes are represented explicitly; a runtime cannot be disposed while sessions remain unless disposal is deferred through native reference counting.
- Native handle wrappers are reference-counted or otherwise protect native-backed Dart views.
- Every size calculation checks integer overflow and platform range.
- Strings crossing the ABI are UTF-8 with explicit lengths where embedded NUL handling matters.
- Calls never retain pointers to temporary FFI arenas.
- Native callbacks do not re-enter Dart from arbitrary ORT worker threads in the initial design.
- Unloading the ORT dynamic library is disabled by default. The loader handle remains valid until process exit.
- A malformed or unsupported model returns a typed error rather than terminating the process where ORT itself returns an error status.

## 1.6 Packaging requirements

- Use Dart build hooks as the primary native-asset mechanism for supported Dart/Flutter toolchains.
- Pin the Dart SDK floor to a release that supports build hooks. Treat link-hook/tree-shaking support as a separately gated enhancement.
- Commit generated FFI bindings and verify regeneration in CI.
- Maintain a machine-readable lockfile containing ORT source/binary versions, URLs, hashes, build flags, deployment targets, and EP dependencies.
- Provide separate artifacts/flavors rather than one binary with every heavy EP dependency.
- Package provider shared libraries next to the matching ORT library.
- Include notices, licenses, build manifests, and an SBOM for release artifacts.
- Verify the final executable/application package, not only intermediate package contents.

## 1.7 Android/sherpa-onnx requirements

- Support a `process` runtime mode in which sherpa-onnx's shared `libonnxruntime.so` is the sole ORT owner.
- The wrapper shim in that mode has no link-time dependency on ORT and resolves `OrtGetApiBase` dynamically.
- The wrapper checks the actual runtime version and C API at startup.
- The final APK/AAB contains one `libonnxruntime.so` for each included ABI.
- The package rejects or clearly diagnoses a statically embedded sherpa ORT when process sharing is requested.
- QNN/custom EP use requires an aligned shared ORT build consumed by both components.
- CI tests both load orders: wrapper first and sherpa first.
- CI covers debug/release, R8/minification where relevant, ABI splits/app bundles, and 16 KB page-size compatibility.

## 1.8 Non-goals for the initial release

- Training APIs.
- ONNX Runtime GenAI APIs.
- Web/WASM support.
- Automatic model download or model registry behavior.
- Transparent conversion from framework-specific model formats.
- A universal “fastest provider” promise.
- Arbitrary native plugin loading from untrusted paths.
- Exposing the entire experimental ORT C API before it stabilizes.
- Sharing native session handles between Dart isolates.

These may be added through separate packages after the core ABI and lifecycle model are stable.

## 1.9 Release milestones

### 0.1: portable CPU MVP

- C shim and runtime negotiation.
- Dense numeric tensors, model metadata, session/run.
- CPU builds on all five OS families.
- Deterministic disposal and finalizer safety net.
- Basic build hooks and artifact manifest.

### 0.2: mobile and coexistence

- String tensors and complete metadata.
- XNNPACK and CoreML qualification.
- Android sherpa-owned process mode.
- Final artifact inspection tooling.
- Async worker isolate API.

### 0.3: desktop acceleration

- CUDA/TensorRT, DirectML, OpenVINO, and oneDNN flavors where qualified.
- Provider options, cache management, I/O binding, session pools.

### 0.4: aligned Android acceleration

- Reproducible aligned sherpa/ORT builds.
- QNN qualification and context-cache workflow.
- Legacy NNAPI mode with deprecation notice.

### 1.0

- Stable Dart API and shim ABI policy.
- Required ONNX value types documented and tested.
- Complete platform/flavor support matrix.
- Reproducible releases, notices, SBOM, and upgrade process.
- No unresolved high-severity ownership, packaging, or load-order defects.
