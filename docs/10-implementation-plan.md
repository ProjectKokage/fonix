# 10. Implementation Plan

The coding agent should implement vertical slices that produce runnable packaged evidence. Do not attempt all providers before the portable CPU path and ownership model are stable.

## Active development order

The current development sequence is narrower than the phase inventory below:

The active scoped pre-1.0 CPU work contains four OS target rows and five
compositions: macOS arm64 bundled, Linux x86_64 bundled, iOS arm64 linked
device, and Android arm64 application-owned bundled plus sherpa-owned process
mode. Android QNN and Windows qualification are not hidden completion gates for
that scope; the exclusions and retained portable regressions below remain
explicit.

The completed macOS arm64 CPU, Android arm64 CPU/XNNPACK functional emulator,
and corrected Android sherpa four-tuple coexistence gates are standing
regressions and must stay green. The sherpa validator and aggregate remain
offline-consistency-only; their trusted capture manifests are retained as a
separate provenance layer. The iOS arm64 CPU reference source has passed 72/72
tests and analysis, including 25/25 focused smoke tests and 5/5 project-contract
tests, plus two consecutive linked simulator Debug builds. Its complete
unsigned-device Release `.app` build/static audit and installed-simulator gate
result is `PASS (2026-08-07)` for the exact epoch recorded in
[`../VALIDATION.md`](../VALIDATION.md). It is now a standing regression. Keep
physical-device execution, approved signing/provisioning, and
iOS CoreML/XNNPACK/GPU/Neural Engine qualification as separately evidenced
follow-up work when hardware and credentials exist; they are not the next
development slice.

1. execute and calibrate the implemented Linux x86_64 CPU reference-package
   gate on the exact clean Ubuntu 18.04.6/glibc 2.27 target host, then cover
   Linux arm64 or accelerated flavors only on matching target hardware; the
   committed scaffold, export closure, final-tree/ELF/provenance auditor, and
   gate tests are source evidence only until that run passes;
2. finish cross-platform correctness and performance stabilization:
   cancellation/stale-work suppression, repeated initialization/disposal,
   bounded queues/profiles, public API/shim ABI review, representative model
   corpus, and measured baselines for rows intended to be advertised; and
3. finish scoped pre-1.0 release engineering for the qualified targets: hosted
   target lanes where feasible, artifact reproducibility, final-package audits,
   SBOM/notices/checksums, security policy, licensing, signing, clean-machine
   installation, and explicit support-matrix promotion. This can close the
   in-scope lanes, but the five-OS product cannot become globally
   `Release-ready` or 1.0 while its Tier-1 Windows target-host gate is deferred
   unless a separate product decision changes that scope.

Android QNN/aligned-ORT device qualification is deferred until the exact SDK,
hardware, firmware, sherpa, licensing, and redistribution inputs exist. All
Windows target-host inference, provider, final-application/package, installer,
and clean-machine qualification work is deferred until a Windows development
environment exists. Existing Android QNN contract/tamper checks and Windows
source/cross-build/security checks remain required and must stay green; neither
deferral creates a support claim. These are scheduling deferrals, not a support
policy change: Windows x64 remains Tier-1 and its target-host lane must resume
before a global `Release-ready` or 1.0 claim.

Implementation checkpoint (2026-08-07): trusted POSIX native, Phase-3,
Linux/desktop audit, binding, macOS runtime, and Apple application/gate command
paths now share online stdout/stderr caps, incremental strict UTF-8 validation,
monotonic deadlines, and bounded TERM/KILL/direct-child-reap cleanup for their
spawned process groups. Native and Phase-3 CTest execution has a 300-second
per-test timeout inside a separate 1,800-second suite deadline. Residual
inherited group members or output pipes after direct-child exit fail the
operation. A Darwin-only 50-millisecond post-exit settlement window drains and
rechecks transient process-group `EPERM`, accepts only later `ESRCH`, and avoids
signaling a numeric group after permission made its post-reap identity
ambiguous. Apple, Linux, and Android reference gates invoke their final
auditors in-process to avoid nested session owners while preserving closed
serialized-report bounds and the former no-bytecode-write contract. Apple
paths scrub loader-injection variables; binding tools also scrub POSIX dynamic
loader variables. The Android loader binds the auditor and its shared helper
to the selected repository without retaining an ambient-module mutation.
Expected nonzero probes require clean settlement; bounded
iOS plist input uses a private temporary file. The macOS provider probe stages
and re-verifies a private regular-file copy named for the locked runtime's
`@rpath` install-name basename, then uses that one snapshot for every static
inspection and provider probe under a closed system-tool environment. This is
a process-group contract for trusted tools, not a sandbox or arbitrary
descendant containment; deliberate
`setsid`/`setpgid` escape remains outside it. Windows uses direct-child
fallbacks only, and Job Object ownership and descendant-tree cleanup remain
deferred and unclaimed. This integration does not alter platform support.

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

- CPU runtime loads on Linux and macOS development targets. Windows
  source/cross-build and secure-loader regressions pass; its target-host load
  requirement resumes with the deferred Windows lane.
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
session cleanup, and double close. The same committed source now includes an
arm64-only iOS 15.1 linked CPU scaffold. Its 72 application tests and analysis,
25 focused smoke tests, five project-contract tests, and two consecutive
simulator Debug builds pass. The complete unsigned-device Release `.app`
build/static audit and transport-normalized, audited-identity-bound simulator
install/run/cleanup gate result is
`PASS (2026-08-07)`. The device output is static-only and unexecuted; the
simulator receipt is exact Debug CPU/full-assignment evidence only. It proves
neither physical-device/signing/distribution nor iOS CoreML/XNNPACK/GPU/Neural
Engine qualification or performance.

This closes the macOS and Android standalone development reference slices and
the exact iOS simulator CPU slice. Android API 24 execution,
physical-device/x86_64 execution, standalone
16 KiB execution, XNNPACK performance/thermal/provider qualification, iOS
physical-device/provider qualification, Linux, and the deferred Windows
target-host gates remain open. The separate sherpa-owned path now has its exact
API 35 arm64-v8a release-minified 4 KiB/16 KiB coexistence matrix.

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
sustained benchmarks/thermal evidence, and Android NNAPI qualification remain
evidence gates, not inferred claims. The iOS reference baseline adds linked CPU
simulator inference only (`PASS (2026-08-07)`); it adds no CoreML,
XNNPACK, GPU, or Neural Engine assignment or performance evidence.

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

The corrected checkpoint is bound to commit
`8a9b6812c17237aeab7ec6d933f23932668c4b33` and source-manifest SHA-256
`3728b31a38c246328cbeda7af14a7ba502489f6e89046371d2c22d29f264efd2`.
The Android sherpa gate, executed on a macOS arm64 host, produced a
45,070,602-byte API 35 arm64-v8a release-minified APK
(`35cca3502504b07d2d21fd27cb46fd5833fab9c6337115adfd5a309818a28ac3`)
and 26,045,592-byte base-only AAB
(`ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`).
The static manifest/report hashes are
`99ccfb2f88eb16b9a7fdd3e03edf39c1529f771dedf210f5e481d85b3afd60e9`
and
`6ad082a3cdf3a498f88504e24a47eb6e239a0d62f13ea02e11b7fbe202e4764e`.

That exact APK and harness
(`5460d723423515d519819e6e369646e6f6cd70edb89760c02da133da40cb7f23`)
passed both load orders with distinct challenges on API 35 arm64 emulators with
page sizes 4096 and 16384. All four two-cycle trusted-`adb` captures passed
cancellation, stale suppression, recovery, both disposal orders, double
disposal, and zero pending work. The capture/receipt/validation hashes are:

- 4 KiB `dart-first`:
  `d5cf010cb70782bb9d542b0d3b68e8272b676d127f3c72679ad24c21ebb40821`,
  `bfc81d2e02e88c0223687cde15bb0b1df91bb2feea37c912bd729092ffc29bce`,
  `8c9da03a622cd6e97883396d6a9a8ff9ca5ee01c0ec477aaff12f6caf319c4ff`;
- 4 KiB `sherpa-first`:
  `6ab621e2f11ed7e3ce3b08b1bb60c1b228dc5f6385f6cabdf1685474fdb06abe`,
  `224cded3e1070074b98e3ee8b9c3b1296699e7e3606b0bdc70bc290cfbadf2ef`,
  `fc0f89c630b4ace1517e59db70fce6771b57a65a5040e151624666a01c4d501a`;
- 16 KiB `dart-first`:
  `56f13fa49e079c60a3deec3da4ed2068a3abe7a9409fe23d099dd87fa0b14e9c`,
  `0ecd917d2d0fe2b1e93e570dd5c802878d4f638c7f64e61f09b5eb0345d91067`,
  `82b811ba6f7f51e3793a76b4816e1d41e909478c3c41e21a33c8f6088f2b855f`;
- 16 KiB `sherpa-first`:
  `2c3371ce831eaae566c8abda5a6da9fe46ee857a23b919a5c69a4304ed646764`,
  `145f1d98440b896adfda283e5a4dde8a943ac2fda3de5b5210ba9268e943147d`,
  `e7255259522aec2c1fd8342ea68e1796d9a961f339127fdfc394d371cb91aa63`.

The four validation records passed schema-2 aggregation; its manifest SHA-256
is `0eb7fece2610b0225060696aa8599a960d01955cc1a7bc6f2ba8d7596964f504`.
Capture provenance remains separately retained as `trusted-adb-capture`, while
every schema-1 validator record and the aggregate remains
`offline-consistency-only`. This completes Phase 7 for the exact API 35
arm64-v8a release-minified APK tuple only. API 24, physical-device,
AAB-derived-install, other ABI/build, performance, signing, distribution, and
general Android support remain open.

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

## Phase 10: scoped release stabilization and future 1.0

The work below applies first to the exact tuples selected for the scoped
pre-1.0 release. It does not satisfy the separate global 1.0 acceptance while
Windows target-host qualification is deferred.

### Work

- Freeze/review public Dart API and shim ABI.
- Complete composite value and error behavior.
- Fuzz/native robustness testing.
- Full support matrix and migration guide.
- Reproducible release pipeline, signing, SBOM, notices.
- Performance regression thresholds.
- Security response process.

### Scoped pre-1.0 acceptance

- Every exact advertised target/provider gate passes.
- Deferred and unadvertised tuples remain explicitly unsupported.
- No unresolved critical/high ownership/packaging defect affects an advertised
  tuple or shared boundary.
- Compatibility floor and each advertised sherpa matrix are documented.
- Licensing, security, signing, SBOM/notices, and publication approval cover
  the exact release contents.
- The release can be reconstructed from archived source/lock/build evidence.

### Global 1.0 acceptance (deferred)

- All Tier 1 and advertised provider gates pass.
- 1.0 API/ABI policy approved.
- Every shared correctness and release condition above passes across the
  complete Tier-1 matrix.

Implementation checkpoint (2026-08-06): the composite value surface, strict
JSON protocols, public diagnostics, support/migration matrix, deterministic
source/SBOM/release evidence, native sanitizer suites, and offline benchmark
receipt validator are implemented. The benchmark validator recomputes bounded
statistics and emits only a `measurement-only` aggregate. A 1.0 release remains
blocked on the explicit target/provider rows above, project licensing, private
security contact, signing/publication authority, reviewed stable performance
baselines/thresholds, and API/ABI release approval.

The 2026-08-08 scoped-release stabilization checkpoint freezes the resolved
public Dart declaration surface and the structural native C ABI/export surface
in separate canonical review records. Minimum-SDK CI reconstructs both records
before binding regeneration. These drift gates support review and versioning;
they do not classify compatibility, prove a target binary, or replace the
external API/ABI approval required by the scoped candidate gate.

The 2026-08-08 shared-runtime hardening checkpoint makes published value-graph
validation read-only under concurrent sessions, retains failed native profiling
disable state until retry or owner disposal while blocking run-options reuse,
bounds isolate string retention even for empty strings, accounts for requested
output names in the worker request limit, and retires a worker
through a later valid cleanup-only ready port after malformed startup
ownership. Focused regressions cover each ownership and resource boundary;
the shared-value regression is mandatory in a separate Linux x64 TSan CI lane
and also passed locally with AppleClang TSan.

The follow-up portable-lifecycle checkpoint moves native termination set/unset
calls outside the process-wide cancellation-registry lock while serializing
same-token settlement, preserves retry ownership after either native failure,
and permanently exhausts rather than wraps the monotonic token space. Worker
startup errors now require a retirement acknowledgement, uncaught errors and
exit share one ordered lifecycle port, and active input reservations remain
held until observed exit. The strict JSON pre-decoder enforces its exact
container-depth bound even for empty leaves and has a deterministic mutation
corpus. Provider discovery and model/custom-metadata fault tests now require
bounded partial owners to be released on error and prove an oversized returned
count cannot authorize entry iteration.

The follow-up native-allocation checkpoint routes all wrapper-owned heap and
aligned allocations through one internal seam while leaving the public ABI and
production activation unchanged. Its bounded POSIX regression enumerates 29
success-path fail-Nth points across status/string, runtime loading, provider
discovery, session options, session construction/metadata, and tensor creation.
Every injected failure neutralizes outputs, returns an allocation or emergency
status, releases partial owners, and permits a clean retry. A fake ORT `Run`
also returns an owned output with an error; the shim releases it exactly once,
preserves the authoritative ORT error, and recovers on the next successful run.
Normal and Address/UndefinedBehavior sanitizer suites pass on the development
host. Broader seeded worker lifecycle stress and allocation enumeration for
less common composite, external-data, and profiling paths continue within this
phase.

The follow-up source-closure checkpoint validates release ZIP and gzip-compressed
Git tar archives without extraction. It binds the archive's declared revision,
closed member inventory, exact manifest-listed bytes, implied directories, and
executable semantics to the current repository baseline through the same
retained descriptor that passed the candidate size and SHA-256 check. Scoped
approval requires the exact canonical derived closure record; the result is
offline consistency evidence only, not archive provenance, reproducibility,
signing, readiness, or publication authority.

The 2026-08-08 CPU measurement checkpoint adds a reproducible 4.29-GFLOP
static-weight MatMul with exact input/reference bytes and a bounded
macOS/Linux final-application target path. The path separates runtime, session,
input, inference, and output-copy timing; requires observed stabilization;
records 100 warm samples and three one-second throughput windows; captures
phase-labelled total RSS; profiles strict CPU assignment outside timing; and
settles every owner before publishing a path-free measurement-only fragment.
One local macOS arm64 Release run passed the complete target path.

The follow-up collection-v2 checkpoint implements a closed protocol-v2 target
fragment, exactly five challenge/PID-bound fresh launches, one raw
host-observation sidecar, unchanged canonical-application-tree observation,
unique target-reported runtime-basename binding, native build-contract binding,
deterministic collection-schema-2 derivation, and an independent offline replay
validator. The validator reopens the exact seven-file raw bundle
and emits only a schema-1 `offline-consistency-only`, `measurement-only`
record. Comparability is closed to `baseline-comparable`, `incomplete`, or
`non-comparable`; only the first may enter a later baseline/threshold review.
The source binding is not compiled provenance, no distribution archive is
claimed, supplied native members are not independent loaded-byte proof, and the
validation record cannot replace the raw samples. Current production host
observers cannot yet emit `baseline-comparable`; controlled power/thermal
capture remains open.

Controlled target baselines, reviewed thresholds, the Linux target-host run,
and measurements for every other selected row remain open. Neither the
historical single macOS launch nor implementation of the collection tools
makes a performance, qualification, support, or release claim. The phase
inventory below remains the long-term program: Phase 9 target qualification
and the Windows target-host portions of Phases 4, 8, and 10 remain deferred.

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
14. iOS CPU packaging sample (baseline implemented; complete gate
    `PASS (2026-08-07)`; physical-device/provider qualification remains
    separate).
15. Android standalone sample.
16. Linux/macOS packaging samples (Linux source/gate implemented; exact
    target-host PASS pending); Windows final sample deferred.
17. Generic provider API and diagnostics.
18. XNNPACK qualification.
19. CoreML qualification.
20. Android sherpa external flavor.
21. Android final-artifact verifier.
22. Desktop provider flavors (non-Windows qualification may proceed; Windows
    target-host/provider qualification is deferred).
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
