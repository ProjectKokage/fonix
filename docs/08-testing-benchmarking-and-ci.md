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
and requests it from the caller isolate, proving that both reach the same
real-shim registry. This complements, rather than replaces, the blocking C
`Run` test.

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
analysis/tests, builds R8 Release APK/AAB bytes, and audits both before any
install. The package evidence covers exact assets, a closed base-only AAB,
manifest/security inventory, singular native ownership, ELF identity/exports,
complete matching development signatures, and static 16 KiB alignment. When a
named AVD is supplied, a separate receipt binds the installed APK hash and app
UID to the exact API/ABI/page-size environment and CPU result. Omitting the AVD
produces no runtime evidence.

## 8.7 Android coexistence tests

See document 7 for the full matrix. Automated gates include:

- inspect exact sherpa AAR/source output;
- reject multiple ORT owners across wrapper/sherpa inputs;
- reject final APK/AAB with missing or duplicate ORT paths;
- verify process-mode shim has no `DT_NEEDED` ORT;
- runtime version/API assertion;
- Dart-first and sherpa-first load order;
- alternating Dart/sherpa inference;
- release/R8 build;
- final-package `--require-16k-page-alignment` ELF gate plus a 16 KB page-size environment;
- aligned QNN build on selected hardware;
- intentionally incompatible runtime produces a controlled error, not a crash.

Standalone checkpoint (2026-08-07): the application-owned arm64 CPU reference
APK/AAB passed the package gate, and the audited APK ran on an API 35 arm64
emulator with a queried 4096-byte page size. This validates neither an actual
16 KiB runtime nor any sherpa AAR, load order, alternating workload, or
coexistence behavior.

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

Implementation checkpoint (2026-08-06): native fake tests cover bounded
provider discovery, exact-one-file profile capture/cleanup, ambiguous JSON,
and cancellation ownership. On macOS arm64, exact ORT 1.27.1 tests pass for a
CPU run with full-assignment evidence and for CoreML `CPUOnly` with strict full
assignment, CPU numerical parity, and the scoped cache. The same CPU evidence
receipt passes through the real worker-isolate protocol. The CoreML test needs
normal host filesystem access because CoreML creates an OS-managed compilation
workspace; its failure in the restricted filesystem sandbox was reproduced as
a sandbox-only condition and the same test passed outside it. These checks do
not qualify ANE use, iOS hardware, XNNPACK performance, or Android NNAPI
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

The Python job includes unit and tamper coverage for the Android auditor and
reference-gate orchestration. The full Gradle package gate and emulator run are
currently explicitly provisioned local evidence, not a hosted CI lane; its
Gradle dependency graph also lacks dependency-verification metadata, so it is a
CI Release-mode package gate rather than a reproducible release build.

The inexpensive C source-quality lane is deliberately offline and
toolchain-independent. `tool/ci/check_c_source_quality.py` walks the closed
repository-owned C/H roots and rejects symlinks, inventory/size overflow,
non-UTF-8 or non-LF input, tabs, trailing whitespace, unbounded lines,
indented preprocessor directives, merge markers, and format/lint suppression
markers. The native CMake jobs remain the semantic compiler-lint gate through
`/W4 /WX` on MSVC and `-Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror`
elsewhere. This lexical gate does not claim `clang-tidy` static-analysis
coverage; adding a pinned cross-platform analyzer remains separate work.

Flutter iOS/Linux/Windows final-application smoke, Android XNNPACK and sherpa
coexistence, and Windows/Linux target-host real-ORT inference are not present CI
jobs. The Android standalone CPU gate has local API 35 arm64/4 KiB evidence but
no hosted job, API 24 runtime, actual 16 KiB runtime, physical arm64 run,
x86_64 run, or installed AAB-derived split. These remain target-evidence
requirements before promoting the corresponding support rows; a cross-build or
source test must not be reported as a substitute.

### Future nightly/scheduled lanes

- Linux arm64 and Windows arm64 where infrastructure permits.
- Physical iOS device CoreML/XNNPACK.
- Physical Android arm64 and 16 KB configuration.
- Android API 24 and x86_64 standalone reference runs.
- QNN device qualification.
- CUDA/TensorRT GPU runner.
- DirectML representative adapters.
- OpenVINO CPU/GPU/NPU runners.
- MIGraphX AMD GPU runner where that flavor is published.
- native WebGPU representative D3D12/Vulkan/Metal runners where published.
- TensorRT RTX plugin and Vitis AI/vendor hardware lanes where published.
- leak/sanitizer/stress suites.
- sustained benchmarks and trend analysis.

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
