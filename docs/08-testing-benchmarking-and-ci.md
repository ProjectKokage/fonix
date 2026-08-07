# 8. Testing, Benchmarking, and CI

## 8.1 Testing philosophy

The project crosses Dart GC/lifetimes, a C ABI, dynamic loaders, ONNX Runtime, provider SDKs, and application packaging. Unit tests alone cannot establish correctness. Tests must cover four products:

1. source/API behavior;
2. native binary behavior;
3. packaged application behavior;
4. model/provider behavior on real hardware.

A release claim is valid only at the highest layer relevant to the claim.

## 8.2 Test model corpus

Keep small, redistributable ONNX fixtures with source/generation scripts and
licenses. The baseline CPU/source corpus includes:

- identity for each wrapper-supported fixed-width dense element type;
- scalar rank-zero model;
- dynamic-shape add/matmul;
- multiple inputs/outputs and nontrivial names/UTF-8 metadata;
- string tensor input/output;
- bool tensor normalization;
- zero-length dimension where supported;
- sequence/map/optional models before 1.0;
- model with external data;
- invalid/truncated/malformed bytes for error handling.

The following are provider- or performance-qualification inputs, not evidence
that can be synthesized as part of the portable baseline corpus. Add and bind
them to the owning target receipt before making the corresponding claim:

- a convolutional floating-point model for XNNPACK/CoreML/mobile EPs;
- a quantized model for CPU/QNN/NNAPI comparisons;
- provider-partition model with known unsupported nodes to test fallback;
- model large enough to exercise I/O binding and sustained benchmark paths.

Generate deterministic inputs and CPU reference outputs. Store model and expected-output hashes.

## 8.3 Dart unit tests

Cover:

- option validation and normalization;
- shape/byte-count overflow;
- element-type mapping;
- metadata conversion;
- exception mapping;
- use-after-dispose;
- idempotent disposal;
- provider ordering and policy;
- diagnostics redaction/serialization;
- worker-isolate protocol and crash handling;
- lockfile/build configuration parsing.

Use fake/native test shim functions only for logic that cannot be exercised with the real CPU runtime. The primary FFI tests use the real shim and ORT.

## 8.4 Native unit tests

Cover:

- loader success/failure and platform error capture;
- shim ABI negotiation;
- ORT API negotiation including an intentionally unsupported version;
- status ownership;
- checked arithmetic/allocation failures;
- handle type/runtime identity validation;
- ref-count and data-lease permutations;
- string conversion and partial failure cleanup;
- runtime registry concurrency;
- output array cleanup on partial run failure;
- bounded cancellation-registry lifecycle, stale-token, saturation, and
  finish/request races;
- a blocking shim `Run` cancelled from another thread, including proof that
  termination is unset only after `Run` returns;
- exported symbol allowlist;
- process mode without link-time ORT dependency.

Use fault injection for allocation and loader failures. Compile sanitizer variants where toolchains/providers permit.

## 8.5 FFI integration tests

For every baseline platform/architecture:

- load runtime and inspect version/API;
- create/release environment/session repeatedly;
- run numeric, string, dynamic-shape, scalar, and metadata fixtures;
- compare outputs with expected values;
- run copy and native-buffer paths;
- hold a native view while disposing its parent, then verify lease safety;
- dispose in every order;
- trigger GC/finalizers under stress without relying on their timing;
- run from a worker isolate;
- cancel/terminate a long run where supported, wait for return, unset termination, and verify deliberate run-options reuse;
- create multiple sessions and session pools.

The isolate suite additionally uses real Dart isolates to reject missing and
unknown protocol fields, verify queue cancellation/backpressure, distinguish
startup/crash/protocol/ORT failures, drain close races, preserve recursive
Optional None types, and surface terminal pool failures before backpressure.
Its opt-in native-asset case registers cancellation inside the worker isolate
and requests it from the caller isolate. The public disposition must report
`nativeTerminationRequested`, proving that the request reached the same
real-shim registry rather than merely being removed from the Dart queue. This
complements, rather than replaces, the blocking C `Run` test and awaiting the
authoritative run settlement.

## 8.6 Packaging tests

Build minimal sample applications for:

- Flutter iOS;
- Flutter macOS;
- Flutter Android;
- Flutter Windows;
- Flutter Linux;
- standalone Dart Native on desktop where supported.

Tests must run against clean environments that do not have a global ORT installation masking packaging defects.

Inspect final artifacts for architecture, dependencies, exports, deployment targets, signatures, and licenses. A compile-only job is insufficient.

Apple sample jobs must set the app project's deployment target and Fonix
`application_minimum_os` independently, prepare the exact manifest/notices as
declared Flutter assets, and run `tool/ci/audit_apple_application.py` against
the final `.app`/device application. The macOS job also runs the packaged CPU
probe with the lock-verified source dylib supplied as `--reference-runtime`; an
adjacent development-directory test is not equivalent evidence. Use
`--platform ios-device` and `--platform ios-simulator` for their respective
final applications so `LC_BUILD_VERSION` cannot confuse device and simulator
slices. The macOS CI job invokes `tool/ci/run_macos_application_gate.py`, which
creates the sample from scratch and records the exact pinned Flutter revision,
14.0 app/hook floors, arm64-only configuration, packaged identity/notices, and
numeric CPU result.

The same job separately invokes
`tool/ci/run_macos_reference_app_gate.py` for the committed `example/` source.
That gate copies the template outside the package checkout, verifies the exact
offline archive, reproduces the committed Flutter assets, runs app-owned
controller/widget tests, builds Release, and binds both the independent bundle
audit and direct public-API one-shot receipt to the named final executable. The
receipt requires exact model/output identities, active CPU-only evidence, full
assignment, and idempotent double close. Process output, time, copied entries,
copied bytes, assets, paths, JSON, and receipt fields are all bounded; loader
override environment variables are removed before launch.

`tool/ci/run_android_reference_app_gate.py` uses the same committed source from
a separate clean copy. It reproduces the Android sidecars, runs Flutter
analysis/tests, builds R8 Release APK/AAB bytes for the selected closed `cpu` or
`xnnpack` profile, and audits both packages before any install. The package
evidence covers the exact four model/manifest assets, a closed base-only AAB,
manifest/security inventory, singular native ownership, ELF identity/exports,
complete matching development signatures, and static 16 KiB alignment. When a
named AVD is supplied, a separate receipt binds the installed APK hash and app
UID to the exact API/ABI/page-size environment and selected profile. The CPU
receipt requires full CPU assignment and double close. The XNNPACK receipt
requires one-node full assignment for six MatMul runs across two sessions,
exact CPU parity, fallback report/rejection, recovery, five total sessions,
double close, and removed profile roots. Omitting the AVD produces no runtime
evidence.

## 8.7 Android coexistence tests

See document 7 for the full matrix. Automated gates include:

- inspect the exact selected sherpa publication/source native inputs under an
  explicit JNI or Flutter FFI profile;
- reject multiple ORT owners across wrapper/sherpa inputs;
- reject final APK/AAB with missing or duplicate ORT paths;
- verify process-mode shim has no `DT_NEEDED` ORT;
- emit one raw schema-2 receipt per exact APK/ABI/load-order/page-size run,
  validate it independently into a schema-1 record, and give only those
  records to the schema-2 compatibility generator;
- bind runtime version/API, fresh challenge, app UID/PID, harness, lockfile,
  target/logcat evidence, and all fixtures;
- prove Dart-first and sherpa-first initialization at both 4 KiB and 16 KiB
  for every claimed ABI/build;
- run 2--64 strict Fonix-reference/Silero-VAD alternating cycles and the closed
  native-cancellation, between-frame VAD cancellation, stale suppression,
  recovery, disposal-order, double-disposal, and zero-pending-work contract;
- release/R8 build;
- final APK and AAB `--require-16k-page-alignment` static gates, with runtime
  evidence bound only to the installed APK until an AAB-derived split is
  installed and exercised;
- intentionally incompatible runtime produces a controlled error, not a crash.

Standalone checkpoint (2026-08-07): independent application-owned arm64 CPU
and XNNPACK profile APK/AAB builds passed the package gate, and each audited APK
ran on an API 35 arm64 emulator with a queried 4096-byte page size. The XNNPACK
receipt is a functional assignment/parity/fallback checkpoint only. These runs
validate neither an actual 16 KiB runtime nor a physical device, API 24 runtime,
x86_64 execution, installed AAB-derived splits, any sherpa publication/load
order/alternating workload, XNNPACK performance or thermal behavior, nor
coexistence.

Coexistence-tooling checkpoint (2026-08-07): the Android native verifier now
recognizes the exact Flutter FFI C API/C++ API topology in addition to legacy
JNI, and the schema-2 compatibility generator accepts repeatable raw native
inputs. It rejects duplicate owners, profile mixing, an unexplained
`libonnxruntime4j_jni.so`, provider-companion drift, and source/final loaded-
segment drift. The schema-2 raw target receipt and its validator also have
strict tamper coverage for APK/library identity, target/logcat/process
bindings, alternating reference workloads, cancellation settlement, stale
suppression, recovery, and disposal. The validator emits a schema-1 record,
and the generator rejects raw receipts, stale-validator records, and an
incomplete four-record matrix. The exact cached 1.13.4 arm64-v8a and x86_64
native inputs pass the static profile and 16 KiB checks. No real sherpa APK
load-order/lifecycle record has been produced, so no coexistence execution is
inferred from this tooling checkpoint.

Commit `635e97d` adds real bounded Fonix process-runtime and sherpa Silero VAD
application adapters, an authoritative lifecycle publication sink,
deterministic fixture generation plus strict asset loading, and a trusted
one-tuple target runner; commit `eaa195a` stabilizes the staged build and
tests. The runtime-provisioned
`run_android_sherpa_reference_app_gate.py` passed on a macOS arm64 host for an
arm64-v8a R8 Release APK and matching base-only AAB. It statically audited all
eight exact qualification assets and the single-ORT graph. The report SHA-256
is `17dbf5c2cc1cd2d76300034721ad49ea41bb5c8cad236ef15f89b3b00026e6fa`,
the gate-time `eaa195a` implementation tree's `MANIFEST.sha256` has SHA-256
`3cabcad849ca2480a0b7d6e9bff17b176f295ac4369ebf7d7057e5c1934f1f8b`,
and the static package manifest SHA-256 is
`8be28263a802f757cd66b42f39234111bc0d99aeb3df8cd069cdb2b4eff34765`.
The 42,979,622-byte APK has SHA-256
`d6032230cebfa6fb2ab89be170579b4b1b848adb0c0485b1ac084e9e9e8a46e8`;
the 26,045,591-byte AAB has SHA-256
`fe39a7870067e1b0b5691ba9f1ce94fa48b9bfd8640b0125410d27bdecc2aa3e`.

That gate report records `targetEvidence: null`. The API 35 arm64 emulator and
its queried 4096-byte page size were environment inventory only: no install,
app launch, workload, or target-runner receipt was produced. The trusted
capture path now exists in source, but it must still be run for both 4 KiB load
orders and then both 16 KiB load orders. The offline validator still cannot
authenticate the origin of supplied target or logcat JSON; public tool hashes
are identities, not attestations. Current validator and compatibility outputs
remain mechanically labeled `offline-consistency-only`, with target
provenance `unverified`.

## 8.8 Provider qualification

For every advertised provider/model/device tuple:

1. Verify artifact manifest says the EP is built.
2. Verify dependencies load.
3. Append/register provider successfully.
4. Capture assignment evidence.
5. Run numerical parity tests.
6. Run cold/warm benchmarks.
7. Test fallback policy.
8. Test cache miss/hit and invalidation if applicable.
9. Stress lifecycle and repeated runs.
10. Record OS/driver/firmware/power mode.

Provider qualification results are versioned artifacts, not prose claims.

Implementation checkpoint (2026-08-07): native fake tests cover bounded
provider discovery, exact-one-file profile capture/cleanup, ambiguous JSON,
cancellation ownership, and exact XNNPACK option validation/forwarding and
oversubscription rejection. On macOS arm64, exact ORT 1.27.1 tests pass for a
CPU run with full-assignment evidence and for CoreML `CPUOnly` with strict full
assignment, CPU numerical parity, and the scoped cache; the CPU receipt also
passes through the real worker-isolate protocol. On the exact Android arm64 API
35/4096-byte emulator tuple, the audited Release XNNPACK profile fully assigned
a one-node static-weight MatMul for six runs across two sessions, matched the
strict CPU reference exactly at `[7,10,15,22,23,34]`, reported and rejected CPU
fallback as required, recovered after rejection, and cleaned up five sessions
and every private profile root. The CoreML test requires normal host filesystem
access because CoreML creates an OS-managed compilation workspace. These checks
do not qualify ANE use, iOS hardware, physical-device XNNPACK behavior, XNNPACK
performance or thermal behavior, provider qualification, or Android NNAPI
runtime behavior.

## 8.9 Numerical tolerances

Define tolerances by model/output/provider/precision. Use:

- exact equality for integer/string/bool where semantics require it;
- absolute and relative tolerances for floating outputs;
- ULP-based checks where appropriate;
- explicit NaN/Inf handling;
- distribution/task metrics for models where elementwise tolerance is misleading.

Never loosen a global tolerance to make one provider pass. Record the reason for provider-specific tolerance.

## 8.10 Leak and memory tests

Native tests:

- AddressSanitizer and UndefinedBehaviorSanitizer on supported Linux/macOS builds;
- LeakSanitizer where compatible;
- ThreadSanitizer on shim/runtime-registry tests where ORT/provider compatibility permits;
- Windows Application Verifier/ASan where practical;
- Android HWASan/ASan test build where feasible;
- repeated session/value creation under memory profiling.

Dart tests:

- native external memory pressure accounting;
- forgotten disposal finalizer stress;
- worker isolate creation/termination;
- retained-view lifecycle;
- peak RSS trend over repeated create/run/dispose cycles.

Set regression thresholds based on stable baselines; account for provider caches intentionally retained for process lifetime.

## 8.11 Benchmark design

Separate:

- shim/runtime load;
- session/model initialization;
- provider compilation;
- first inference;
- warm inference;
- sustained throughput;
- data preparation/copy;
- device transfer;
- cache creation/load.

Use warm-up criteria based on observed stabilization, not an arbitrary single run. Report distribution statistics and raw samples/artifacts.

### Measurements

- latency p50/p95/p99 and max;
- throughput at defined concurrency;
- CPU utilization;
- native/total RSS and peak memory;
- host-to-device/device-to-host copies when observable;
- mobile battery/energy proxy and thermal state;
- session creation and cache sizes;
- final application/native binary size.

### Controls

- fixed model hash and inputs;
- fixed provider options;
- fixed thread counts and power mode;
- same precision/batch/shape;
- CPU reference run;
- record fallback/assignment;
- isolate cold and warm cache cases;
- randomize/interleave provider order where thermal drift matters.

### Offline benchmark evidence receipts

Target harnesses produce receipts; the repository validator does not run a
benchmark or synthesize hardware results. A version-1 receipt follows
[`benchmark_receipt.schema.json`](../templates/ci/benchmark_receipt.schema.json)
and names only path-free, bounded identity data. The validator requires the
corresponding model, input fixture, reference output, schema-3 native build
manifest, runtime library, final application artifact, and normalized
assignment evidence as separate local files and rehashes all seven. The
normalized assignment file follows
[`provider_assignment_evidence.schema.json`](../templates/ci/provider_assignment_evidence.schema.json)
and its provider counts must exactly match the receipt. For a lock-selected
artifact, the target provider and reported name must also occur in the
manifest's compiled provider inventory.

```bash
python3 -B tool/ci/validate_benchmark_receipt.py \
  --receipt /path/to/target-benchmark.json \
  --model /path/to/model.onnx \
  --input-fixture /path/to/input.bin \
  --reference-output /path/to/reference.bin \
  --build-manifest /path/to/native-build.json \
  --runtime-artifact /path/to/libonnxruntime \
  --final-artifact /path/to/final-application-archive \
  --assignment-evidence /path/to/provider-assignment.json \
  --output /new/path/to/validated-benchmark.json
```

The input includes raw integer-microsecond samples for cold runtime load, session
creation, first run, and warm runs. It also includes bounded throughput windows
as completed-run counts plus integer microseconds. Declared p50, p95, p99, and
maximum values use the nearest-rank definition: sort the samples and select
rank `ceil(percentile * count / 100)`. Per-window and aggregate throughput use
integer milli-runs per second, rounded half up. The validator recomputes every
value and rejects any mismatch, non-finite number, count/size overflow, unknown
field, or duplicate JSON key.

The receipt always records concurrency, threads, model/input identity,
runtime/build/artifact identity, normalized provider options, assignment and
fallback, platform/architecture, non-reversible device identity, OS/build,
driver/firmware, power mode, and thermal state. RSS is either measured with raw
samples or explicitly marked unavailable through one closed reason. Binary
sizes are mandatory and rechecked for the final artifact and runtime library.
Cache size is measured for cold/hit runs or explicitly marked as disabled or
inapplicable. Path- or secret-bearing provider option values use only a
non-reversible SHA-256 representation; ordinary strings cannot contain paths,
URIs, or common secret forms.

The emitted aggregate follows
[`benchmark_validation_record.schema.json`](../templates/ci/benchmark_validation_record.schema.json),
contains no input paths, and is bound to the exact input receipt and validator
hashes. Its `claimStatus` is always `measurement-only`. Validation does not
establish a supported platform, provider qualification, a baseline, a
regression threshold, or transferability to another model/build/device tuple.
Those decisions require real target runs, review of stable baselines, and
separately approved thresholds. Focused tamper tests are discovered
automatically by the existing `tool/ci/tests/test_*.py` unittest command.

## 8.12 CI matrix

### Current required workflow

The checked-in pull-request/push workflow currently runs:

- Dart format, analysis, and ordinary tests on the current minimum and stable
  SDKs;
- isolated binding regeneration and an exact diff;
- Python CI-script and standalone verifier tests, the C source-quality gate,
  release-evidence checks, and the closed source checksum;
- deterministic Phase-3 fixture-byte checks on Linux, macOS, and Windows;
- warning-as-error native CTests on Linux x64 and macOS arm64 with the
  documented sanitizers, plus the Windows x64 shim/security contract without
  real ORT inference; and
- the exact ORT 1.27.1 macOS arm64 runtime, native bridge, worker lifecycle,
  Dart value/provider profile, build-hook, freshly generated final Flutter
  application gate, and committed public-API reference-application gate.

The Python job includes unit and tamper coverage for the Android auditor,
schema-2 load-order receipt validator, schema-1 validation-record ingestion,
four-record compatibility aggregation, reference-gate orchestration, and the
trusted one-tuple target-runner contract. The full Gradle package gates and the
standalone CPU/XNNPACK emulator runs are explicitly provisioned local evidence,
not hosted CI lanes. Their Gradle dependency graphs also lack dependency-
verification metadata, so these remain CI Release-mode package gates rather
than reproducible release builds. The sherpa target runner has not run.

The inexpensive C source-quality lane is deliberately offline and
toolchain-independent. `tool/ci/check_c_source_quality.py` walks the closed
repository-owned C/H roots and rejects symlinks, inventory/size overflow,
non-UTF-8 or non-LF input, tabs, trailing whitespace, unbounded lines,
indented preprocessor directives, merge markers, and format/lint suppression
markers. The native CMake jobs remain the semantic compiler-lint gate through
`/W4 /WX` on MSVC and `-Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror`
elsewhere. This lexical gate does not claim `clang-tidy` static-analysis
coverage; adding a pinned cross-platform analyzer remains separate work.

Flutter iOS/Linux/Windows final-application smoke, Android sherpa target
coexistence, and Windows/Linux target-host real-ORT inference are not present
CI jobs. The Android standalone CPU and XNNPACK gates have local API 35
arm64/4 KiB final-package evidence. The sherpa-owned path now also has local
runtime-provisioned Release APK/AAB static evidence, but its report contains
`targetEvidence: null`. None of these paths has a hosted job, API 24 runtime,
actual 16 KiB runtime, physical arm64 run, x86_64 run, or installed
AAB-derived split. The XNNPACK run is a functional emulator checkpoint, not
performance, thermal, or provider-qualification evidence. These remain
target-evidence requirements before promoting the corresponding support rows;
a cross-build, source test, static package audit, or tiny emulator fixture must
not be reported as a substitute.

The active target roadmap is to run the sherpa-owned Flutter FFI Release APK
through `dart-first` and `sherpa-first` on the queried 4 KiB arm64 emulator,
then repeat both load orders on an actual 16 KiB environment. After that come
iOS arm64, Linux x86_64, and stabilization/release work. Android aligned/QNN
device work is deferred until its exact licensed SDK/hardware tuple exists.
All Windows target-host, final-application, installer, and clean-machine work
is deferred until a Windows environment exists. Their present QNN
tamper/static gates and Windows source/cross-build/loader-security gates remain
required.

### Future nightly/scheduled lanes in active scope

- Linux arm64 where infrastructure permits.
- Physical iOS device CoreML/XNNPACK.
- Physical Android arm64 and 16 KB configuration.
- Android API 24 and x86_64 standalone reference runs.
- CUDA/TensorRT GPU runner.
- OpenVINO CPU/GPU/NPU runners.
- MIGraphX AMD GPU runner where that flavor is published.
- native WebGPU representative D3D12/Vulkan/Metal runners where published.
- TensorRT RTX plugin and Vitis AI/vendor hardware lanes where published.
- leak/sanitizer/stress suites.
- sustained benchmarks and trend analysis.

Deferred lanes are Android QNN device qualification and Windows x64/arm64
target-host, DirectML, final-package, installer, and clean-machine runs. Keep
their source/static/security jobs green without scheduling them as the next
development slice.

### Release

- all Tier 1 target packages and sample apps;
- complete provider qualification for advertised flavors;
- exact sherpa compatibility matrix;
- final APK/AAB/IPA/app/desktop artifact inspection;
- SBOM/notices/checksums/signing;
- clean-machine install/run tests;
- reproducibility record.

## 8.13 Suggested CI job names

```text
lint-dart
bindings-regenerate
native-linux-x64-asan
native-macos-arm64
native-windows-x64
flutter-android-x64-emulator
flutter-android-arm64-device
android-sherpa-dart-first
android-sherpa-sherpa-first
android-16k-pages
flutter-ios-simulator-arm64
flutter-ios-device-coreml
flutter-macos
flutter-linux
flutter-windows
provider-cuda
provider-tensorrt
provider-directml
provider-openvino
provider-migraphx
provider-webgpu-native
provider-nv-tensorrt-rtx-plugin
provider-vitisai
provider-qnn
artifact-audit
release-sbom
```

Jobs should use descriptive skip/unavailable states rather than silently passing without hardware.

## 8.14 Failure artifacts

Retain on failure:

- wrapper/runtime diagnostic JSON;
- ORT logs/profiling with redaction;
- final native-library inventory;
- ELF/PE/Mach-O dependency/export reports;
- model/input hashes and output diff summary;
- benchmark environment and raw samples;
- Android logcat/iOS/macOS/Windows/Linux crash logs;
- sanitizer reports;
- Gradle dependency graph and merged native library directory for Android.

Do not upload model/input data that is confidential; fixtures should be public and deterministic.

## 8.15 Acceptance gates

A change cannot merge when it:

- weakens lifecycle tests;
- adds a native binary without a digest/license record;
- adds an EP without assignment and numerical tests;
- changes Android packaging without final-artifact inspection;
- introduces `pickFirst` for ORT;
- changes shim ABI without versioning/migration tests;
- changes compatibility-floor API without sherpa regression coverage;
- claims a platform/architecture that only compiles but does not run;
- changes provider defaults without benchmark evidence.

A concise checklist is provided in [`../templates/ci/acceptance-checklist.md`](../templates/ci/acceptance-checklist.md).
