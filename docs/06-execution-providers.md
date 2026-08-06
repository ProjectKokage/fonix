# 6. Execution Providers

## 6.1 Principles

Execution providers (EPs) are not interchangeable flags. Each provider changes native dependencies, supported operators/types/shapes, initialization cost, memory transfers, threading, numerical behavior, and concurrency constraints.

The wrapper must:

- expose ordered provider configuration;
- normalize provider options without hiding them;
- separate artifact availability from runtime/provider activity;
- validate provider-required session settings;
- make fallback visible;
- qualify performance per model/device/runtime;
- publish heavy providers as separate flavors;
- preserve CPU as a portable reference unless a strictly accelerated product intentionally excludes it.

ORT assigns subgraphs according to provider capabilities. Partial assignment can be slower than CPU because of partition boundaries and transfers. Session creation success is not proof of acceleration.

## 6.2 Recommended platform matrix

| Platform | Baseline | Preferred optional paths | Specialized/legacy |
|---|---|---|---|
| iOS | CPU/MLAS | XNNPACK for floating-point CPU workloads; CoreML for qualified models/devices | reduced mobile builds |
| macOS | CPU/MLAS | CoreML; XNNPACK | native WebGPU as a separately qualified flavor |
| Android | CPU/MLAS + XNNPACK-capable mobile artifact | XNNPACK; QNN HTP on qualified Snapdragon devices | NNAPI legacy; aligned custom builds |
| Windows | CPU/MLAS | DirectML, CUDA/classic TensorRT, OpenVINO, QNN depending hardware | native WebGPU, TensorRT RTX plugin, Vitis AI/Windows ML as separate integrations |
| Linux | CPU/MLAS | CUDA/classic TensorRT, OpenVINO, oneDNN, XNNPACK, MIGraphX | native WebGPU, TensorRT RTX plugin, and vendor-specific source/plugin builds |

“Preferred” means worth evaluating, not unconditionally faster.

## 6.3 Provider selection policy

The public API composes policy from three explicit pieces:

- the ordered `OrtSessionOptions.providers` list;
- each provider's `OrtProviderRequirement` (`preferred`, `required`,
  `requireActive`, or `requireFullAssignment`); and
- `OrtFallbackPolicy` (`allow`, `report`, `rejectCpu`, or `rejectAny`).

Every provider in the list is an explicit registration request.
`requireActive`, `requireFullAssignment`, and `rejectAny` require a validated
one-run profile before output publication; `report` and `rejectCpu` do so when
the list contains a non-CPU provider. Fonix has no automatic
recommendation, “auto fastest”, strict preset, or benchmark preset; callers
construct those policies explicitly from the closed values above.

In the current API, `preferred` and `required` both require successful
registration; neither silently removes an unavailable provider. The two
assignment requirements add stronger run-time proof. This keeps missing native
dependencies visible instead of turning them into an implicit policy change.
CPU-only `report` does not create a profile because it has no accelerator
fallback claim to establish.

### Provider naming is an adapter concern

Keep these identifiers separate:

| Namespace | XNNPACK example | Purpose |
|---|---|---|
| Stable Dart/wrapper ID | `xnnpack` | Public configuration and serialization |
| ORT registration name/mechanism | `XNNPACK` or a version-specific factory/plugin path | Native session-options registration |
| ORT-reported/display name | `XnnpackExecutionProvider` for the locked API-27 map | Discovery, logs, and diagnostics |

The exact registration name and available API vary by ORT version/build and EP. Maintain a versioned native mapping, test it in every flavor, and include the resolved mapping in diagnostics. Do not copy Python provider strings into the C registration API by convention.

The wrapper-level `cpu` entry also declares fallback/order policy. Since the default CPU EP may already be present implicitly, the native adapter must not assume it always needs a generic append call.

### API-27 implementation checkpoint

The source tree now has closed Dart and native registration adapters for the
initial desktop provider set:

| Wrapper ID | API-27 registration path | Native target gate |
|---|---|---|
| `cuda` | CUDA V2 create/update/append/release option object | Windows/Linux |
| `tensorrt` | TensorRT V2 create/update/append/release option object | Windows/Linux |
| `directml` | `GetExecutionProviderApi("DML", 27, ...)` and `OrtDmlApi` append | Windows |
| `openvino` | documented generic registration name `OpenVINO` | Windows/Linux |
| `dnnl` | oneDNN create/update/append/release option object | Windows/Linux |
| `migraphx` | public `OrtMIGraphXProviderOptions` struct append | Linux |

The Dart constructors emit exact canonical key/value maps and reject unknown
or non-canonical values even when a known ID is passed through `named`. The
native ABI repeats the closed-key checks, enforces `TensorRT -> CUDA` ordering
and DirectML's session constraints, and never routes these provider-specific
IDs through the generic append API. Unsafe pointer-bearing options such as
external allocators, user streams, engine/cache paths, and oneDNN thread-pool
arguments are intentionally absent.

This checkpoint proves source compilation and fake API-table behavior only.
It does not add heavy runtime artifacts or prove provider availability,
dependency loading, model compatibility, node assignment, numerical accuracy,
performance, packaging, or hardware qualification on any target.

## 6.4 CPU/MLAS

CPU is the universal reference and fallback.

Guidance:

- Use CPU first for quantized mobile models unless measurement shows another EP wins.
- Expose intra-op/inter-op controls, but choose conservative defaults.
- Avoid oversubscription with XNNPACK, application isolates, audio threads, or multiple sessions.
- Measure single-thread and bounded-thread behavior.
- Preserve a CPU-only validation path for output parity.
- Consider reduced-operator and prepacked-weight sharing only after correctness.

Do not describe CPU as “unaccelerated”; MLAS contains architecture-specific optimized kernels.

## 6.5 XNNPACK

Targets: Android, iOS, Windows, Linux, and potentially selected macOS builds.

Strengths:

- portable optimized floating-point CPU kernels;
- simple mobile deployment in official ORT mobile packages;
- often a strong starting point for non-quantized mobile models.

Requirements:

- configure XNNPACK thread count coherently with ORT session threading;
- avoid two competing intra-op thread pools;
- measure dynamic-shape behavior and operator coverage;
- capture partitioning/fallback;
- compare with CPU for quantized models rather than assuming improvement.

Recommended provider order for a floating-point mobile baseline:

```text
XNNPACK -> CPU
```

Use `requireActive`/`requireFullAssignment` with a rejecting fallback policy
during qualification, and use `report` when production needs evidence without
automatic rejection.

## 6.6 CoreML

Targets: iOS and macOS.

CoreML can use Apple CPU, GPU, and Neural Engine. The ORT CoreML EP has distinct model-format and compute-unit options.

Guidance:

- qualify on physical Apple Neural Engine devices;
- prefer static input shapes when the model permits, as dynamic shapes may reduce performance;
- choose NeuralNetwork vs MLProgram according to deployment target and model support;
- expose compute-unit selection explicitly;
- support a model cache directory with a key tied to model content, ORT version, provider options, and application schema;
- compare cold compile/model-load time separately from warm inference;
- enable compute-plan profiling only in diagnostics/development modes;
- do not claim “ANE execution” merely because CoreML registered.

Typical order:

```text
CoreML -> CPU
```

XNNPACK and CoreML should not be layered automatically without model-specific evidence. Evaluate separate sessions/policies.

The typed cache API accepts only an existing app-owned root, an exact
caller-declared lowercase model SHA-256, and a bounded application schema. The
caller cannot provide `ModelCacheDirectory`. Fonix derives a non-reversible
SHA-256 identity over those values, normalized non-path CoreML options,
negotiated ORT version, the full structured shim build/owner/source/artifact
policy, OS/architecture, and wrapper cache schema. Native code creates only
that final direct child, rejects link/non-directory substitutions, and passes
the canonical path to ORT. Default diagnostics expose the identity, never the
root. This does not claim that Fonix hashed an unread model file.

## 6.7 QNN

Targets: supported Qualcomm Snapdragon devices on Android and Windows.

QNN is an advanced, SDK-coupled flavor. Android generally requires building ORT with the exact Qualcomm AI Engine Direct SDK and packaging the required backend libraries.

Guidance:

- publish/consume QNN as an aligned flavor, not as a toggle on a generic ORT binary;
- pin ORT, QNN SDK, backend library, architecture, and model preparation pipeline;
- prefer QDQ quantized models for HTP where required by the supported operator path;
- make backend choice (`HTP`, `GPU`, `CPU` where applicable) explicit;
- qualify device/firmware/driver combinations;
- use QNN context caching to reduce startup only with a rigorous cache key and invalidation policy;
- validate output parity/tolerance and unsupported-node fallback;
- provide a mode that rejects CPU fallback during HTP qualification;
- do not bundle proprietary SDK content without its redistribution terms.

Android with sherpa-onnx requires one aligned ORT. Build sherpa against that ORT; do not add a second QNN-enabled `libonnxruntime.so` beside sherpa's default one.

## 6.8 NNAPI

NNAPI is available through ORT on Android, but Android deprecated NNAPI in Android 15 and warns that future devices are expected to rely largely on the CPU backend. Treat it as a legacy compatibility EP.

Policy:

- never make NNAPI the new default;
- retain an opt-in provider constructor for existing deployments;
- clearly expose OS/device requirements;
- measure against XNNPACK and CPU on each device;
- report partial partitioning and driver behavior;
- maintain migration notes toward XNNPACK, QNN, or another vendor-supported path;
- isolate NNAPI-specific flags from the modern generic provider API.

## 6.9 CUDA

Targets: Windows and Linux with compatible NVIDIA driver/runtime dependencies.

Guidance:

- publish a CUDA-specific artifact/flavor;
- pin the ORT CUDA package/build and supported CUDA/cuDNN dependency range from that release's official matrix;
- package only redistributable dependencies permitted by their licenses or document application prerequisites;
- expose device ID, arena strategy, memory limits, and closed convolution/tuning controls through validated provider options; keep pointer-bearing user-stream and external-allocation hooks outside this ABI;
- support warm-up and optional CUDA graph workflows only after model qualification;
- measure host/device copies and use I/O binding for steady-state workloads where valuable;
- surface provider DLL/shared-library load errors distinctly;
- do not let a CPU-only machine fail to load a CPU artifact because CUDA dependencies were included in the same flavor.

Typical order without TensorRT:

```text
CUDA -> CPU
```

## 6.10 TensorRT

Targets: qualified NVIDIA Windows/Linux deployments.

TensorRT normally complements CUDA. Recommended order:

```text
TensorRT -> CUDA -> CPU
```

Guidance:

- pin TensorRT, CUDA, cuDNN, ORT, driver requirements, and architecture;
- separate engine-build/cold-start metrics from warm inference;
- expose engine/timing cache directories with atomic writes and strict cache keys;
- never share an engine cache across incompatible model/runtime/device/provider settings;
- inspect TensorRT partition coverage and fallback to CUDA/CPU;
- test dynamic-shape profiles and memory limits;
- do not enable FP16/INT8 without model accuracy qualification;
- isolate engine creation from latency-critical first request where the product permits precompilation.

## 6.11 DirectML

Target: Windows.

DirectML offers broad DirectX 12 hardware reach. ORT's DirectML EP requires:

- sequential session execution mode;
- memory pattern optimization disabled.

The Dart convenience API must enforce these constraints before FFI.

Additional policy:

- serialize runs on a session unless provider-specific tests establish another safe pattern;
- use session pools for throughput rather than assuming same-session parallelism;
- publish DirectML as a separate flavor;
- qualify adapter selection and output precision;
- document that DirectML is in sustained engineering and evaluate Windows ML/AutoEP as a future Windows-specific package rather than silently replacing behavior.

Order:

```text
DirectML -> CPU
```

## 6.12 OpenVINO

Targets: primarily Windows and Linux on Intel CPU/GPU/NPU.

Guidance:

- pin an ORT/OpenVINO compatibility tuple rather than accepting any installed OpenVINO;
- package or provision the exact runtime according to official redistribution guidance;
- expose device selection (`CPU`, `GPU`, `NPU`, `AUTO`, `HETERO`, `MULTI`) as validated strings/options;
- keep advanced OpenVINO configuration in a structured JSON/string field with schema validation where practical;
- measure model compilation/startup and steady state separately;
- test fallback and multi-device behavior;
- avoid global environment setup as the only supported deployment path.

Order usually starts:

```text
OpenVINO -> CPU
```

but exact device/multi-device selection belongs to OpenVINO options.

## 6.13 oneDNN

Targets: selected Windows/Linux CPU deployments.

Use when measured server/desktop CPU performance justifies the larger dependency/flavor. Avoid publishing it in the portable baseline. Pin runtime dependencies, inspect partitioning, and compare against MLAS on representative hardware.

## 6.14 Additional specialized accelerator EPs

These EPs matter for a high-performance wrapper, but they should not inflate the portable package or be represented as universally supported. They use the same stable wrapper-ID, flavor, diagnostics, assignment, and benchmark contracts.

### AMD MIGraphX

MIGraphX accelerates ONNX models on AMD GPUs and is the forward-looking AMD GPU path to evaluate; the older ROCm EP is deprecated in the current ORT provider summary. Treat MIGraphX as a Linux/AMD GPU flavor unless the exact upstream release documents and CI proves another target.

Requirements:

- pin ORT, MIGraphX, ROCm, driver, GPU architecture, and container/libc assumptions;
- keep the dependency stack out of the CPU artifact;
- expose device ID, FP16/INT8, graph compilation/cache, and stream options only through the exact release schema;
- distinguish model compile time from warm runs;
- compare against CPU and any applicable vendor-native baseline;
- validate dynamic shapes, unsupported partitions, and numerical tolerances.

### NVIDIA TensorRT RTX

TensorRT RTX is distinct from the classic TensorRT EP. Current ORT guidance deprecates the built-in TensorRT RTX EP in favor of the standalone EP ABI plugin. The wrapper should therefore implement it through the trusted plugin/device API in a dedicated flavor, not add another permanent built-in factory path.

Requirements:

- pin the standard ORT runtime, TensorRT RTX plugin, driver, and companion libraries as one compatibility tuple;
- enumerate/select the intended EP device after plugin registration;
- key AOT context and runtime caches by model, plugin, ORT, GPU architecture, options, and schema;
- report AOT/JIT compilation separately from inference;
- do not conflate TensorRT RTX cache portability claims with classic TensorRT engine compatibility;
- retain classic `TensorRT -> CUDA -> CPU` support as a separate flavor and policy.

### Native WebGPU

The native WebGPU EP uses Dawn and maps to platform graphics APIs such as D3D12, Vulkan, or Metal. It can provide a cross-vendor desktop GPU path, but its dependency size, backend/driver maturity, operator coverage, shader compilation, and interaction with the host application's graphics stack require dedicated qualification.

Requirements:

- publish it as a separate native flavor and pin Dawn/backend revisions;
- expose adapter/device selection and backend identity in diagnostics;
- measure shader/pipeline compilation and cache behavior separately;
- validate thread/device loss and application shutdown behavior;
- avoid assuming a Flutter rendering GPU/device can be shared with ORT;
- do not use this native EP as a substitute for a future Dart Web/WASM package.

### Vitis AI and other vendor EPs

Vitis AI covers heterogeneous AMD targets, including Windows Ryzen AI NPUs and Linux adaptive/embedded accelerators. CANN, Rockchip NPU, Arm-focused, and other vendor EPs may also be valuable for product-specific deployments.

Policy:

- add each as an application/vendor-owned flavor with its exact compiler, quantizer, firmware, driver, and runtime tuple;
- make offline model compilation/quantization artifacts part of the versioned model identity;
- do not place preview/vendor SDK dependencies in the default package;
- use plugin EPs for new integrations where upstream supports them;
- publish support only for named hardware/OS combinations backed by CI hardware and output/performance qualification.

## 6.15 Plugin EP libraries and Windows ML

Current ONNX Runtime releases provide plugin EP library registration and EP-device selection APIs. Treat this as an optional, version-gated extension of the wrapper rather than as unrestricted dynamic loading.

A plugin-capable flavor must:

- require an absolute, trusted, application-local plugin path or a package-owned resolver;
- register the library under an application-controlled registration name;
- enumerate `OrtEpDevice` instances and select the intended device(s), rather than assuming registration alone activates hardware;
- keep the plugin library registered and loaded until all dependent sessions, devices, allocators, and callbacks are destroyed;
- unregister only through an ordered shutdown path;
- pin the ORT/plugin/driver-or-SDK compatibility tuple and package companion libraries adjacently;
- verify signatures/digests, license inventory, exports, and transitive dependencies;
- expose plugin registration, selected device, and assignment evidence in diagnostics;
- fail closed if the negotiated C API does not contain the required plugin/device functions.

QNN 2.x is distributed as a plugin EP for its currently supported package targets, but this does not replace the Android integration described here. The official Android QNN path still requires an Android ORT source build with QNN integrated, so Android plus sherpa remains an aligned single-runtime build unless a specifically qualified Android plugin distribution is selected in the future.

The initial package should not accept arbitrary plugin paths from untrusted input. A generic plugin API belongs in an experimental/advanced sublibrary with an application-supplied trust policy.

Windows ML/AutoEP should be evaluated as a Windows-specific higher-level integration. It may choose providers differently from raw ORT and should not be conflated with a stable cross-platform C wrapper.

## 6.16 Provider options

At the native ABI, represent provider options as UTF-8 key/value arrays. At Dart level:

- typed builders validate known options;
- provider-specific IDs reject unknown options even through the generic named constructor;
- generic provider maps remain bounded escape hatches only for registration mechanisms explicitly present in the closed native API-27 map;
- booleans are normalized to the exact expected textual form;
- the typed CoreML cache root is absolute, normalized, app-owned, and
  model-scoped;
- generic and QNN option values are bounded and redacted when path-like, but
  are not treated as policy-approved filesystem paths;
- secrets/private paths are redacted in diagnostics;
- duplicate keys are rejected;
- normalized options appear in redacted provider diagnostics and, for CoreML
  caching, are included in the scoped cache identity.

Provider option schemas are versioned against ORT/provider releases. Do not assume a key remains valid forever.

## 6.17 Assignment verification

Qualification must answer:

- Did the provider load?
- Did registration succeed?
- Which nodes/subgraphs were assigned?
- Which nodes fell back and why?
- What transfers/partitions occurred?
- Was the expected device/backend actually used?

Use ORT profiling/logging and provider-specific diagnostics. Store a normalized evidence artifact in CI/benchmark results. Where assignment cannot be reliably determined for an ORT version, mark the capability unavailable and do not claim full acceleration.

Fonix's implemented assignment receipt parses one ORT Chrome-trace model-run
span and its contained node kernel spans. It rejects duplicate JSON object
keys (including escape-equivalent keys), unknown provider names, multiple or
empty model runs, invalid node fields, excessive bytes/events/depth, and node
spans outside the model run. With a non-CPU provider, `report` attaches
evidence without rejecting; CPU-only `report` needs no profile.
`requireActive`, `requireFullAssignment`, `rejectCpu`, and `rejectAny` enforce
their independent conditions before output publication.

## 6.18 Numerical validation

For each EP/model:

- use a CPU reference with deterministic inputs;
- define absolute/relative tolerances per output/type;
- include edge cases and representative data;
- detect NaN/Inf differences explicitly;
- validate quantized/dequantized semantics;
- test repeated runs and varying dynamic shapes;
- test cache hit/miss equivalence;
- record precision modes such as FP16/INT8.

Performance is irrelevant if output acceptance fails.

## 6.19 Benchmark protocol

Report:

- hardware model, OS build, driver/firmware, power mode;
- ORT/shim/provider versions and options;
- model hash, opset, input shapes/types;
- graph assignment evidence;
- cold runtime load;
- cold session/model compile;
- first run;
- warm p50/p95/p99;
- throughput at defined concurrency;
- peak/RSS/native memory;
- mobile thermal state and sustained performance;
- cache state;
- CPU fallback and transfer counts where available.

Do not compare providers with different preprocessing, batch sizes, precision, or cache state without disclosing it.
