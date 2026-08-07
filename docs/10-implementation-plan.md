# 10. Implementation Plan

The coding agent should implement vertical slices that produce runnable packaged evidence. Do not attempt all providers before the portable CPU path and ownership model are stable.

## Active development order

The current development sequence is narrower than the phase inventory below:

The completed macOS arm64 CPU, Android arm64 CPU, and Android arm64 XNNPACK
functional emulator gates are standing regressions and must stay green. The
prior Android sherpa static and two-load-order 4 KiB results are historical
exact-artifact evidence only and cannot promote support.

1. rebuild the Android sherpa APK from a corrected committed tree, run
   `dart-first` and `sherpa-first` on both 4 KiB and 16 KiB environments,
   retain trusted-runner provenance, validate all four raw schema-2 receipts,
   and aggregate the four fresh records into the schema-2 compatibility
   manifest; offline record validation does not authenticate target origin;
2. produce the iOS arm64 CPU/CoreML reference application; verify final
   simulator packaging/inference first, then physical-device lifecycle,
   assignment, parity, and fallback evidence when hardware/signing are
   available;
3. produce and exercise the Linux x86_64 CPU reference package on a clean
   target host, then cover Linux arm64 or accelerated flavors only on matching
   target hardware;
4. finish cross-platform correctness and performance stabilization:
   cancellation/stale-work suppression, repeated initialization/disposal,
   bounded queues/profiles, public API/shim ABI review, representative model
   corpus, and measured baselines for rows intended to be advertised; and
5. finish release engineering for the qualified targets: hosted target lanes
   where feasible, artifact reproducibility, final-package audits,
   SBOM/notices/checksums, security policy, licensing, signing, clean-machine
   installation, and explicit support-matrix promotion. This can close the
   in-scope lanes, but the five-OS product cannot become globally
   `Release-ready` or 1.0 while its Tier-1 Windows target-host gate is deferred
   unless a separate product decision changes that scope.

Android QNN/aligned-ORT device qualification is deferred until the exact SDK,
hardware, firmware, sherpa, licensing, and redistribution inputs exist. All
Windows target-host inference, final-application packaging, installer, and
clean-machine qualification work is deferred until a Windows development
environment exists. Existing Android QNN contract/tamper checks and Windows
source/cross-build/security checks remain required and must stay green; neither
deferral creates a support claim.

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
- Windows secure DLL layout/search (source/cross-build regression gate only
  while target-host work is deferred).
- Final artifact audit scripts and sample apps.
- Licenses/notices/SBOM scaffold.

### Acceptance

- Same CPU inference sample runs on iOS, macOS, Android, and Linux; Windows is
  required when its deferred target-host lane resumes.
- Clean-environment tests do not find global ORT.
- Exports/dependencies/architectures match allowlists.
- Android 16 KB page-size checks pass.
- Apple packaged application load succeeds, not just unit tests. Windows
  packaged loading remains deferred with the Windows target-host lane.

Implementation checkpoint (2026-08-07): a committed macOS arm64 Flutter
reference app exercises only the public Fonix library through one bounded
worker-isolate CPU session. App-owned tests cover startup/run retry,
cancellation and stale completion, suspend/resume replacement and oscillation,
shutdown during startup/run/suspend, synchronous-listener reentrancy, double
close, and compact UI layout. Its clean-copy gate
reproduces exact assets, analyzes/tests/builds Release, verifies the local
ad-hoc signing exception, runs the independent final-bundle audit, and obtains
an exact full-CPU-assignment receipt from the packaged executable. The shared
reference source also produces independent arm64-v8a R8 Release APK/AAB builds
for the closed CPU and XNNPACK profiles. Every Android final package passes the
exact static audit. On an API 35 arm64 emulator with a queried 4096-byte page
size, the CPU APK produces its unchanged full-assignment/double-close receipt,
while the XNNPACK APK proves one-node full assignment for six MatMul runs across
two sessions, exact CPU parity, fallback report/rejection, recovery, five-
session cleanup, and double close. This closes the macOS and Android standalone
development reference slices only. Android API 24 execution, an actual 16 KiB
runtime, physical-device/x86_64 execution, XNNPACK performance/thermal/provider
qualification, the complete 4 KiB/16 KiB sherpa coexistence matrix, iOS,
Linux, and the deferred Windows target-host gates remain open.

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

Implementation checkpoint (2026-08-07): the provider discovery/registration
adapter, typed CPU/XNNPACK/CoreML/legacy-NNAPI options, canonical generic
XNNPACK thread option and oversubscription guards at both Dart and native
boundaries, schema-bound CoreML cache, immutable provider-state diagnostics,
automatic bounded per-run profiling, strict fallback/assignment enforcement,
and direct/worker evidence receipts are implemented. Exact ORT 1.27.1 macOS
arm64 CPU and CoreML `CPUOnly` assignment/parity/cache tests pass. The final
Android arm64 reference APK also passes the closed XNNPACK functional profile
on an API 35/4 KiB emulator: six one-node MatMul assignments across two
sessions, exact CPU parity, explicit fallback report/rejection, recovery, and
cleanup. This tiny emulator fixture does not satisfy Phase 6 provider
qualification. Physical-device XNNPACK assignment, representative workloads,
sustained benchmarks/thermal evidence, iOS/ANE, and Android NNAPI qualification
remain evidence gates, not inferred claims.

## Phase 7: Android sherpa-owned runtime

### Work

- Build external/process shim with no ORT link dependency.
- Integrate the exact selected sherpa publication/source native inputs and
  revision into a dedicated Android Flutter FFI sample app, starting with one
  arm64-v8a Release artifact.
- Add exact raw-`jniLibs` inventory plus closed APK/AAB static coexistence
  gates. The final-package gates must require the shim, ORT, selected Flutter
  FFI consumers, complete dependency graph, and source-to-final byte bindings;
  use the implemented `android_static_package_manifest.py` pair gate rather
  than the verifier's preliminary `sherpa-audit` policy alone.
- Implement Android constrained runtime resolution.
- Emit schema-2 target receipts for Dart-first and sherpa-first on both page
  sizes. Prove exact alternating Fonix-reference/Silero-VAD cycles, active
  Fonix native cancellation settlement, honest between-frame VAD cancellation,
  stale-result suppression, recovery, both disposal orders, double disposal,
  and zero pending work.
- Capture installation, package-manager/`adb` observations, and raw logs in a
  trusted device runner tied to the exact APK; do not promote caller-supplied
  JSON merely because its hashes are internally consistent.
- Validate every raw receipt into a schema-1 record, then generate the schema-2
  compatibility manifest from the four-record matrix.
- Keep the matching AAB as a separate static audit until its delivered split
  can be installed and exercised.

### Acceptance

- Wrapper artifact contains no ORT.
- Final package has one ORT per ABI.
- Runtime reports actual version/API.
- Both load orders pass independently on 4 KiB and 16 KiB environments for one
  exact release/R8 APK, with four current-validator records and retained
  trusted-runner capture provenance.
- Exact Fonix bytes, bounded Silero VAD invariants, cancellation, stale
  suppression, recovery, and lifecycle settlement pass.
- The corresponding APK and AAB pass static audit; only an installed and
  exercised delivered split can add AAB runtime evidence.
- Incompatible runtime fails cleanly.
- No `pickFirst` or merge-order workaround exists.

Implementation checkpoint (2026-08-07): Android composition requires the
closed `sherpa` or `application` runtime owner, emits respectively a process
shim or a bundled exact artifact, and embeds schema-3 owner/source policy.
Locked arm64-v8a and x86_64 shims cross-build at API 24 and pass static 16 KiB,
dependency, and single-ORT inventory checks. The deterministic compatibility
record generator distinguishes the legacy JNI and current Flutter FFI
profiles, accepts repeatable exact `jniLibs`/Native Assets source directories,
binds every selected native library to final loaded segments, and rejects
unexplained final native libraries. A strict schema-2 target-receipt validator
binds the exact sherpa-owned Flutter FFI APK, evidence process, fixtures,
alternating workload, and lifecycle, emits schema-1 validation records, and
requires four current records before schema-2 aggregation.

The historical checkpoint ran while the checkout reported commit
`0e4effef837352685ab5ac39cd28e017973fca70`; its `MANIFEST.sha256` file had
SHA-256
`e0080a0ed914fd55b5ab3e02f2593ab2c9a1c490ebf0e2129d9c2a806acfc5d6`.
That commit omitted required handwritten `lib/src/build/native_versions_lock.dart`,
while the manifest included generated ignored state. The exact APK/static/4 KiB
results below remain historical exact-artifact evidence, but establish no
source-final, commit-bound, reproducibility, or support-promotion claim.
The macOS arm64 gate produced a 45,070,602-byte arm64-v8a R8 Release APK
(`93fd4a388a723621b404aabfc1026197d8fb11620fb2f3d2746bac594fd81e54`)
and 26,045,592-byte base-only AAB
(`ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`).
The static manifest/report hashes are
`a75a3f7f7004a86453cf9d6d6f686c1e5645eaf167dd44268b67d107a3b099d3`
and
`b63dd44133b8a74baa02d810e9e54905d6732a4f62e6099cf56c3bc90f28fd14`.

That exact APK and harness
(`963e76730336bf9c22e2641d0a28b066a2e9592d64eef2723d80b81da7217519`)
passed `dart-first` and `sherpa-first` with distinct challenges on one API 35
arm64 emulator with page size 4096 and fingerprint hash
`acb4e14882d5e2e5cd4b91925de599cdc88a39b95d37a63ebe48c15f3000f384`.
Both two-cycle trusted-`adb` captures passed cancellation, stale suppression,
recovery, both disposal orders, double disposal, and zero pending work, and
left the package absent. The respective capture/receipt/validation hashes are
`6eb2a5df4ef1b2b541522f1448cfde27ddae705d5fd810f717956a945792ad40`,
`fe72465d43b0981ca6b4d6fad18f4c63579b833369a85656c1e89a34bcb357c8`,
`44a27782535653538616556cabbbe918e0c0efec299315e182b5b0a9fb0f1293`
and
`2171e8eaf7685c5bcb59cf68195ddfc81fd23372926df45809f1c8c0a58815d0`,
`414b85ecf40598ec9433f9c48232d634d087b0901e34ae1e76b0c06309cd4f1b`,
`72f52d31edce29f5bdbba8cfbdc664043d45324e90b23780fe2124588bb45dbd`.
Capture provenance is `trusted-adb-capture`; schema-1 validation remains
`offline-consistency-only`. Phase 7 is still partial: a corrected rebuild and
fresh 4 KiB/16 KiB four-record matrix are next. API 24, physical-device,
AAB-derived-install, performance, and distribution evidence remain open.

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

Windows target-host provider and package qualification in this phase is
deferred with the rest of the Windows target roadmap. Keep the provider
adapters, fake-table tests, PE cross-build, and secure-loader contract green.

## Phase 9: aligned Android/QNN (deferred target qualification)

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
Do not schedule target qualification until those exact inputs exist; continue
running the existing contract, static linkage, and tamper gates.

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
5. Windows loader (source/cross-build complete; target-host deferred).
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
16. Linux/macOS packaging samples; Windows final sample deferred.
17. Generic provider API and diagnostics.
18. XNNPACK qualification.
19. CoreML qualification.
20. Android sherpa external flavor.
21. Android final-artifact verifier.
22. Desktop provider flavors.
23. QNN aligned build (target qualification deferred).
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
