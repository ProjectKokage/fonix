# 2. Architecture

## 2.1 Layered design

```text
Application / higher-level model package
              │
              ▼
Idiomatic Dart API
  sessions, tensors, providers, diagnostics, worker isolates
              │
              ▼
Generated dart:ffi bindings
  project-owned dort_* ABI only
              │
              ▼
C shim
  validation, ownership, status translation, runtime loader,
  provider registration, tensor/value adaptation
              │
              ▼
ONNX Runtime C API function table
              │
              ▼
CPU EP and optional platform/provider libraries
```

The C shim is an intentional compatibility firewall. ONNX Runtime's C API is stable and versioned, but directly generating a large Dart surface from it would expose function-table layout, allocator conventions, provider-specific legacy entry points, and native lifetime details to Dart. A small project-owned ABI limits breakage and permits runtime resolution on Android.

## 2.2 Components

### Dart public API

Responsibilities:

- immutable configuration objects;
- type-safe values and tensors;
- disposal and use-after-dispose checks;
- native error translation;
- provider policy and diagnostics;
- worker-isolate orchestration;
- copy/native-buffer choices;
- stable semantic versioning.

It must not know the layout of `OrtApi`, `OrtValue`, or provider C structs.

### Generated FFI layer

Responsibilities:

- exact bindings for `src/dort.h`;
- symbol annotations/lookup;
- native finalizer entry-point binding;
- no policy or lifecycle logic.

Generated files are committed, not edited manually, and reproduced in CI.

### C shim

Responsibilities:

- own and validate opaque handles;
- load or bind to the selected ORT runtime;
- call `OrtGetApiBase`, query version, and request the configured C API;
- maintain runtime identity and handle compatibility;
- translate `OrtStatus` into project-owned status objects;
- provide stable provider option registration;
- validate dimensions, byte lengths, and element types;
- provide ref-counted handles for native-backed views;
- constrain exported symbols.

### Build/package layer

Responsibilities:

- choose a runtime ownership mode and EP flavor;
- compile the shim for the target;
- obtain or build pinned ORT artifacts;
- place provider dependencies correctly;
- ensure licenses/checksums;
- reject invalid native-library graphs;
- produce diagnostics metadata embedded in the shim or adjacent manifest.

## 2.3 Runtime ownership modes

### Bundled

The package supplies both the shim and a pinned ORT artifact. This is the standalone default when no other dependency owns ORT.

Properties:

- predictable feature set;
- easiest support contract;
- potential collision if another AAR/framework also packages ORT;
- separate EP flavors should be used to constrain dependency size.

### Process-owned

The package supplies only the shim. The shim resolves an ORT already present in the application process/package. This is the default for Android coexistence with sherpa-onnx's shared runtime.

Properties:

- no duplicate `libonnxruntime.so` producer;
- runtime version is discovered, not assumed;
- base feature surface is limited by the negotiated C API;
- provider availability depends on the owner artifact;
- initialization must fail closed when the runtime is absent/incompatible.

### Aligned

The application build produces one ORT artifact and builds all native consumers against it. This is the strongest mode for custom EPs and minimal builds.

Properties:

- exact headers/library/build flags are shared;
- one ORT binary is packaged;
- sherpa-onnx and the Dart wrapper can both use QNN or another custom build only when that combination is validated;
- build complexity is higher but reproducibility and diagnosability are strongest.

### System/explicit desktop path

The application supplies an absolute, trusted path to ORT and its provider dependencies.

Properties:

- suitable for managed desktop installations;
- must use secure DLL/shared-library search behavior;
- must not search arbitrary current-working-directory or global paths;
- not supported on iOS and discouraged on Android.

## 2.4 Runtime identity

Every native object carries or references a `dort_runtime_t` identity. The identity includes:

- canonical library path or process-runtime token;
- ORT version string;
- negotiated C API version;
- build/flavor identifier when available;
- function-table pointer;
- loader handle;
- project ABI version.

The shim rejects an operation that combines objects from different identities. This prevents, for example, creating a tensor with one allocator/runtime and passing it to a session from another runtime.

A process registry may deduplicate runtime opens by canonical identity. It must be synchronized and reference-counted. The loader handle remains open for process lifetime by default because ORT may own global state, worker threads, provider libraries, and callbacks that outlive a naive `dlclose`/`FreeLibrary`.

## 2.5 Environment and session topology

Use one ORT environment per runtime identity unless tests identify a requirement for multiple isolated logging environments. The environment is inexpensive to share and represents global logging/threading context.

```text
Runtime identity
  └── Environment
      ├── Session A
      │   ├── Run options
      │   └── Values
      ├── Session B
      └── Session pool
```

Session options are cloned/consumed during session construction and should not be mutable after being applied unless the public API explicitly builds a new session.

## 2.6 Version negotiation

Startup sequence:

1. Load/locate the ORT library or process symbol.
2. Resolve `OrtGetApiBase`.
3. Call `GetVersionString()` and copy the result.
4. Call `GetApi(required_api_version)`.
5. If null, return a typed incompatibility status before creating any ORT object.
6. Verify the shim ABI requested by Dart.
7. Initialize the shared environment and provider diagnostics.

The base shim in this snapshot targets C API 27 so it can coexist with the observed sherpa-onnx 1.27.x runtime while still running against newer ORT versions that support API 27. Features introduced only in later C APIs belong in a separately negotiated extension or a future shim ABI; do not access function pointers beyond the negotiated API contract.

The runtime version string is informational and useful for allow/deny policies, but the C API negotiation is the binding compatibility gate. Provider ABI/dependency compatibility is checked separately.

## 2.7 Provider architecture

Public provider configuration is normalized as:

```text
Provider request
  id: stable wrapper-level provider ID
  options: ordered string map
  requirement: preferred | required | requireFullAssignment
  fallback: allowCpu | rejectCpu | rejectAnyFallback
```

The Dart API uses stable wrapper IDs such as `cpu`, `xnnpack`, `coreml`, `qnn`, `cuda`, `tensorrt`, `directml`, and `openvino`. Optional qualified flavors may add IDs such as `migraphx`, `webgpu`, `nv_tensorrt_rtx`, and `vitisai`. These are deliberately distinct from both ONNX Runtime's generic C registration names (for example, `XNNPACK`, `CoreML`, or `NvTensorRtRtx`) and provider names reported by discovery APIs/logs (often suffixed with `ExecutionProvider`). A versioned native mapping translates the wrapper ID for the exact ORT build. Never pass a discovered/display name into the registration API without consulting that mapping.

The shim should favor the generic ONNX Runtime provider registration API available at the compatibility floor. Provider-specific Dart convenience classes validate option names/types and normalize values, but the public native ABI remains a wrapper ID plus key/value arrays. Where an EP at the selected compatibility floor is available only through a provider-specific factory or plugin API, the adapter is still hidden behind the same wrapper ID and reports the concrete registration mechanism in diagnostics. Plugin registration is environment-scoped: the runtime identity owns the trusted plugin path, registration token/name, enumerated device descriptors, and ordered shutdown after all sessions are gone.

Provider states are distinct:

1. **Compiled:** build manifest claims the EP is present.
2. **Discoverable:** ORT reports or can load the provider and dependencies.
3. **Registered:** appending the EP to session options succeeds.
4. **Active:** ORT assigns at least one node/subgraph to it.
5. **Qualified:** output and performance tests pass for a named model/device/runtime combination.

Do not collapse these into a single boolean.

## 2.8 Graph assignment and fallback

ORT partitions graphs according to provider capabilities. Unsupported nodes may remain on a lower-priority EP, usually CPU. Partition boundaries can cause memory copies and synchronization that make an accelerator slower.

The wrapper therefore needs:

- explicit provider order;
- strict provider policies;
- session initialization logs/profiling capture;
- a normalized assignment summary when obtainable;
- a benchmark mode that rejects fallback;
- a production mode that can permit fallback while recording it.

`requireFullAssignment` is a wrapper policy. It may be implemented by parsing validated ORT profiling/session logs or another stable introspection mechanism. Until a reliable mechanism exists for a runtime version, the option must report “unsupported,” not silently weaken to “provider registered.”

## 2.9 Tensor ownership model

### Safe-copy path

```text
Dart TypedData
  -> allocate wrapper-native buffer
  -> copy
  -> create OrtValue referring to native buffer
  -> run
  -> output OrtValue
  -> copy to Dart object
```

This is the default because Dart-managed memory must not be retained by native code.

### Native-buffer path

```text
OrtNativeBuffer (wrapper allocation, ref-counted)
  -> Dart view for filling
  -> OrtValue retains buffer
  -> run / I/O binding
  -> explicit disposal or finalizer release
```

Native-backed views require a C-side reference count so disposing the high-level tensor cannot free memory still referenced by a Dart typed-data view/finalizer token.

### ORT-allocated output path

The wrapper retains the `OrtValue` until data is copied or all native-backed views release their references. A raw typed list must never be returned without an owner/finalizer relationship that keeps the allocation alive.

## 2.10 Isolates and concurrency

Native pointers and wrapper objects are isolate-local. The synchronous API runs on the calling isolate and may block it. The asynchronous API uses a long-lived worker isolate with this topology:

```text
UI/main isolate
  -> serializable command + copied/transferable input
worker isolate
  -> owns runtime/session/native buffers
  -> executes synchronously
  -> returns copied/transferable output or a higher-level message
```

Do not send native pointer addresses between isolates as a public feature.

Concurrency policy is provider-sensitive:

- default: serialize calls per session;
- throughput: use a session pool after provider-specific qualification;
- enable concurrent calls on a single session only when ORT/provider documentation and stress tests support it;
- DirectML sessions require sequential execution settings and must not be treated as generally concurrent;
- provider caches and compiled contexts need locking and atomic publication.

## 2.11 Error architecture

Native functions return a null status on success or a project-owned status on failure. A status carries:

- shim error domain/code;
- optional ORT error code;
- operation name;
- UTF-8 message;
- optional provider/runtime context;
- retriable/fatal classification when well-defined.

Dart maps these to typed exceptions such as:

- `OrtRuntimeNotFoundException`;
- `OrtApiIncompatibleException`;
- `OrtProviderUnavailableException`;
- `OrtModelLoadException`;
- `OrtInvalidArgumentException`;
- `OrtRunException`;
- `OrtDisposedException` (prefer a local state error before FFI);
- `OrtNativePackagingException`.

Do not infer retriability from arbitrary message text.

## 2.12 Extension points

The design reserves, but does not require in the MVP:

- custom op library registration through explicit trusted paths;
- I/O binding and device tensors;
- plugin EP discovery from trusted application-local manifests;
- reduced-operator/minimal ORT builds;
- model compilation/context-cache helpers;
- training and GenAI in separate packages.

Each extension must preserve the single-runtime and project-owned ABI rules.
