# 10. Implementation Plan

The coding agent should implement vertical slices that produce runnable packaged evidence. Do not attempt all providers before the portable CPU path and ownership model are stable.

## Active development order

The current development sequence is narrower than the phase inventory below:

1. keep the committed macOS arm64 public-API reference app and its packaged
   lifecycle/inference gate green;
2. build the Android standalone CPU vertical slice, qualify XNNPACK on the
   available emulator/device targets, then integrate and exercise the exact
   sherpa-owned process-runtime composition in both load orders;
3. produce the iOS arm64 CPU/CoreML reference app, simulator gate, and
   physical-device lifecycle/assignment evidence;
4. produce and exercise the Linux x86_64 CPU reference package on a clean
   target host; and
5. finish cross-platform lifecycle stress, public API/ABI review, release
   evidence, licensing, signing, and performance baselines for the advertised
   rows.

Android QNN/aligned-ORT device qualification is deferred until exact SDK,
hardware, firmware, sherpa, and redistribution inputs are available. Windows
target-host inference, final-app packaging, and installer qualification are
also deferred until a Windows development environment is available. Their
existing source, cross-build, and tamper gates remain required and must stay
green, but neither deferral promotes a support claim.

## Phase 0: repository and contracts

### Work

- Create package/repository structure.
- Adopt strict Dart analysis and C formatting/linting.
- Add `native/versions.lock.yaml` schema and parser.
- Add the authoritative `src/dort.h` and ABI-version function.
- Configure build hook to compile/load a no-op shim on one host platform.
- Configure FFI generation and regeneration check.
- Add docs/support matrix and ADR directory.

### Acceptance

- `dart analyze`/format pass.
- C shim builds with warnings-as-errors.
- Dart calls `dort_get_abi_version()` through the normal build-hook asset.
- Generated bindings reproduce without diff.
- No unpinned binary download exists.

## Phase 1: runtime loader and negotiation

### Work

- Implement linked, bundled dynamic, process, and explicit desktop loader abstractions behind platform files.
- Implement secure path handling.
- Resolve `OrtGetApiBase` and copy runtime version.
- Request compatibility-floor API 27.
- Implement runtime identity registry and process-lifetime loader retention.
- Implement project status/error objects.
- Expose `OrtRuntime` and diagnostics.

### Acceptance

- CPU runtime loads on Linux/macOS/Windows development targets.
- Missing library, invalid path, missing symbol, and unsupported API return typed errors.
- Intentionally requesting an unsupported API never dereferences a null table.
- Runtime registry passes concurrent open/release stress.
- Explicit path search does not use current working directory.

## Phase 2: environment, session, and numeric inference

### Work

- Shared environment per runtime identity.
- Session options for logging, optimization, execution/threading, memory arena/pattern.
- Session creation from file and bytes.
- Input/output metadata for dense tensors.
- Native buffer and dense fixed-width tensor creation.
- Named run and output retrieval.
- Idempotent disposal/finalizers.
- CPU fixture suite.

### Acceptance

- Numeric identity/add/matmul models pass.
- Scalar, dynamic-shape, zero-length, and overflow tests pass.
- Every ownership permutation passes native/Dart tests.
- No sanitizer leaks in project shim paths.
- Session creation is separate from run.

Implementation checkpoint (2026-08-06): deterministic real-ORT fixtures now
cover rank-zero float64, symbolic dynamic Add and MatMul with multiple concrete
shapes, zero-length tensors, normalized bools, all signed/unsigned fixed-width
integer types, nontrivial multi-input/output names, and the existing numeric
multiplication path. Exact ORT 1.27.1 Dart and standalone strict-C harnesses
check metadata, shapes, counts, independent expected values, output ordering,
and ownership cleanup. Checked-arithmetic tests cover pre-allocation overflow.

## Phase 3: complete value and metadata safety

### Work

- String tensors.
- Model metadata/custom metadata.
- Float16/bfloat16/raw packed storage utilities.
- Sequences/maps/optionals required for 1.0.
- Data leases/native-backed output views.
- External-data model policy.
- Comprehensive typed exceptions.

### Acceptance

- Fixture suite covers each supported value kind.
- UTF-8/empty/large string cases pass.
- A retained view survives parent disposal and releases exactly once.
- Metadata remains usable after session disposal when documented as copied.
- Invalid external data paths are rejected by sandbox policy.

Implementation checkpoint (2026-08-06): the generated corpus and real-runtime
harness cover strings, float16/bfloat16 raw storage, copied UTF-8 metadata,
unknown-rank tensors, sequences, string/int64 maps, present/empty optionals,
and contained/missing/escaping external data. Dart lease tests retain views and
composite children across parent disposal. Convolutional, quantized,
known-partition, and sustained-size models remain inputs to their future
provider/performance qualification receipts rather than portable baseline
claims.

## Phase 4: all baseline platform packages

### Work

- Dart build hooks for five OS families.
- iOS linked framework/XCFramework path.
- macOS application-local packaging.
- Android standalone mobile flavor.
- Linux `$ORIGIN`/dependency layout.
- Windows secure DLL layout/search.
- Final artifact audit scripts and sample apps.
- Licenses/notices/SBOM scaffold.

### Acceptance

- Same CPU inference sample runs on iOS, macOS, Android, Linux, Windows.
- Clean-environment tests do not find global ORT.
- Exports/dependencies/architectures match allowlists.
- Android 16 KB page-size checks pass.
- Apple/Windows packaged application load succeeds, not just unit tests.

Implementation checkpoint (2026-08-06): a committed macOS arm64 Flutter
reference app now exercises only the public Fonix library through one bounded
worker-isolate CPU session. App-owned tests cover startup/run retry,
cancellation and stale completion, suspend/resume replacement and oscillation,
shutdown during startup/run/suspend, synchronous-listener reentrancy, double
close, and compact UI layout. Its clean-copy gate
reproduces exact assets, analyzes/tests/builds Release, verifies the local
ad-hoc signing exception, runs the independent final-bundle audit, and obtains
an exact full-CPU-assignment receipt from the packaged executable. This closes
the macOS development-reference slice only; the iOS, Android, Linux, and
deferred Windows target gates above remain open.

## Phase 5: async worker and throughput primitives

### Work

- Versioned worker-isolate protocol.
- Worker-owned runtime/session.
- Transfer/copy input and output messages.
- Cancellation/termination mapping.
- Session/worker pool.
- Backpressure and shutdown behavior.

### Acceptance

- UI/main isolate remains responsive during inference test.
- Native handles never cross isolate messages.
- Worker crash and ORT run error are distinct.
- Shutdown frees sessions before isolate termination when possible.
- Pool throughput and memory are benchmarked.

Implementation checkpoint (2026-08-06): the closed protocol, worker-owned
runtime/session, bounded copied/transferable values, serialized queue,
cooperative cancellation, graceful shutdown, and explicit pool are implemented.
Strict native tests cover the 1024-entry registry, races, and cancellation of a
blocking `Run`; Dart tests use real isolates and include an opt-in cross-isolate
real-shim registry proof plus real ORT CPU inference. These correctness checks
do not substitute for the target/model-specific pool throughput and RSS
benchmark required before making a performance claim.

## Phase 6: mobile EPs

### Work

- Generic provider registration.
- Typed CPU/XNNPACK/CoreML/NNAPI convenience options.
- Provider state diagnostics and ORT profiling capture.
- CoreML cache and deployment-target policy.
- Explicit assignment/fallback policy composition and benchmark receipts.
- Mobile model usability tooling in development workflow.

### Acceptance

- XNNPACK and CoreML have real-device assignment, parity, and benchmark evidence.
- CoreML does not claim ANE use without evidence.
- NNAPI is marked legacy/deprecated and opt-in.
- Assignment and rejecting fallback policies fail closed on missing evidence or fallback.
- Thread oversubscription tests cover CPU/XNNPACK.

Implementation checkpoint (2026-08-06): the provider discovery/registration
adapter, typed CPU/XNNPACK/CoreML/legacy-NNAPI options, XNNPACK
oversubscription guard, schema-bound CoreML cache, immutable provider-state
diagnostics, automatic bounded per-run profiling, strict fallback/assignment
enforcement, and direct/worker evidence receipts are implemented. Exact ORT
1.27.1 macOS arm64 CPU and CoreML `CPUOnly` assignment/parity/cache tests pass.
Physical-device XNNPACK, iOS/ANE, and Android NNAPI qualification/benchmark
rows remain evidence gates, not missing implementation paths or inferred
claims.

## Phase 7: Android sherpa-owned runtime

### Work

- Build external/process shim with no ORT link dependency.
- Integrate exact selected sherpa AAR/revision into sample app.
- Add AAR/APK/AAB inventory and one-ORT gates.
- Implement Android constrained runtime resolution.
- Add Dart-first/sherpa-first tests and alternating workload.
- Add compatibility manifest generation.

### Acceptance

- Wrapper artifact contains no ORT.
- Final package has one ORT per ABI.
- Runtime reports actual version/API.
- Both load orders and release/R8 pass.
- 16 KB environment passes.
- Incompatible runtime fails cleanly.
- No `pickFirst` or merge-order workaround exists.

Implementation checkpoint (2026-08-06): Android composition requires the
closed `sherpa` or `application` runtime owner, emits respectively a process
shim or a bundled exact artifact, and embeds schema-3 owner/source policy.
Locked arm64-v8a and x86_64 shims cross-build at API 24 and pass static 16 KiB,
dependency, and single-ORT inventory checks. The deterministic compatibility
record generator binds final package bytes and requires both load orders plus
4/16 KiB receipts. No exact product sherpa artifact, Flutter APK/AAB, target
load-order/workload receipt, or Android device run exists, so acceptance is
only partially complete.

## Phase 8: desktop accelerated flavors

### Work

- CUDA/classic TensorRT, DirectML, OpenVINO, and optionally oneDNN flavors.
- Separately prioritized MIGraphX, native WebGPU, TensorRT RTX plugin, and Vitis AI/vendor flavors where hardware and product demand justify them.
- Provider-specific option validation.
- Trusted plugin registration, device selection, and ordered shutdown for plugin-based flavors.
- Provider dependency packaging.
- I/O binding/device memory where justified.
- Cache management.
- Hardware CI/qualification records.

### Acceptance

- Each published flavor loads on a clean compatible machine.
- Assignment/parity/benchmark evidence exists.
- TensorRT order is TensorRT -> CUDA -> CPU.
- DirectML session constraints are enforced.
- CPU artifact does not require heavy provider dependencies.
- Provider caches reject incompatible identities.
- Plugin-based flavors prove registration/device/lifetime behavior and do not load arbitrary untrusted paths.
- CPU artifacts remain loadable without optional GPU/NPU/plugin dependencies.

Implementation checkpoint (2026-08-06): closed API-27 Dart/native adapters and
option validation exist for CUDA, classic TensorRT, DirectML, OpenVINO, oneDNN,
and MIGraphX. Native fake-table tests cover registration, TensorRT ordering,
DirectML constraints, cleanup, and CPU independence; Windows profiling also
has a secure target-host CTest contract. No heavy provider artifact is in the
current lock and no GPU/NPU target assignment, parity, dependency package, or
benchmark receipt exists.

## Phase 9: aligned Android/QNN

### Work

- Reproducible ORT Android source build with QNN.
- Application-owned native artifact manifest.
- Build sherpa against exact external ORT directories.
- Package exact QNN backend libraries.
- QDQ/context-cache workflow.
- Device/firmware qualification.

### Acceptance

- One aligned ORT per ABI.
- sherpa and Dart both run in one process.
- QNN HTP assignment evidence exists.
- Strict no-CPU-fallback qualification passes for supported model or clearly documents unsupported nodes.
- Context cache cold/hit parity and invalidation pass.
- Proprietary redistribution constraints are respected.

Implementation checkpoint (2026-08-06): the aligned sherpa driver consumes an
exact local git revision, patch set, NDK, external ORT directories, and
optional hash/license-bound QNN manifest, then emits a deterministic receipt.
The QNN qualification validator binds final APK, backend bytes, model/QDQ,
assignment, load orders, parity, timing, and context-cache invalidation. These
contracts pass synthetic tamper tests only; no real QNN SDK, aligned product
build, physical device, HTP assignment, or redistribution decision exists.

## Phase 10: 1.0 stabilization

### Work

- Freeze/review public Dart API and shim ABI.
- Complete composite value and error behavior.
- Fuzz/native robustness testing.
- Full support matrix and migration guide.
- Reproducible release pipeline, signing, SBOM, notices.
- Performance regression thresholds.
- Security response process.

### Acceptance

- All Tier 1 and advertised provider gates pass.
- No unresolved critical/high ownership/packaging defect.
- Compatibility floor and sherpa matrix documented.
- 1.0 API/ABI policy approved.
- Release can be reconstructed from archived source/lock/build evidence.

Implementation checkpoint (2026-08-06): the composite value surface, strict
JSON protocols, public diagnostics, support/migration matrix, deterministic
source/SBOM/release evidence, native sanitizer suites, and offline benchmark
receipt validator are implemented. The benchmark validator recomputes bounded
statistics and emits only a `measurement-only` aggregate. A 1.0 release remains
blocked on the explicit target/provider rows above, project licensing, private
security contact, signing/publication authority, reviewed stable performance
baselines/thresholds, and API/ABI release approval.

## Suggested issue breakdown

Create independently reviewable issues:

1. Lockfile schema and artifact verifier.
2. C ABI/status skeleton.
3. Dart build-hook no-op asset.
4. POSIX loader.
5. Windows loader.
6. Apple linked mode.
7. Runtime registry/version diagnostics.
8. Environment/session options.
9. Numeric buffer/tensor path.
10. Run/result ownership.
11. Metadata and strings.
12. Native data leases.
13. Async worker protocol.
14. iOS packaging sample.
15. Android standalone sample.
16. Linux/Windows/macOS packaging samples.
17. Generic provider API and diagnostics.
18. XNNPACK qualification.
19. CoreML qualification.
20. Android sherpa external flavor.
21. Android final-artifact verifier.
22. Desktop provider flavors.
23. QNN aligned build.
24. SBOM/release automation.

Each issue must name acceptance tests and generated evidence.

## Agent checkpoints

At the end of every phase, the coding agent should produce:

- changed files and requirement mapping;
- commands/tests run;
- packaged target tested;
- diagnostics JSON;
- artifact inspection report;
- provider assignment/benchmark evidence if relevant;
- compatibility and remaining-risk statement.

Proceeding to the next phase without a green vertical slice compounds native packaging failures and is not acceptable.
