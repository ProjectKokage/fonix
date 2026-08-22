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
- provider discovery and model metadata errors that return partial owners,
  including valid bounded arrays that must be released and oversized counts
  that must never authorize entry iteration;
- bounded cancellation-registry lifecycle, stale-token, saturation, and
  finish/request races, including blocked native set/unset calls that cannot
  block unrelated tokens, set/unset failure retry, and non-wrapping token-space
  exhaustion;
- a blocking shim `Run` cancelled from another thread, including proof that
  termination is unset only after `Run` returns;
- concurrent runs in separate sessions sharing one published input value,
  proving recursive validation remains read-only;
- profiling-disable fault injection, including no partial bytes, fail-closed
  run-options reuse, successful retry, and native-owner release before private
  directory cleanup;
- POSIX profiling replacement, root-permission loss, and unexpected nonempty
  child cleanup, including neutral output, cleanup-error precedence, retained
  owner retry, concurrent run rejection throughout that retry, failed child
  descriptor acquisition under a restrictive umask without residue, exact
  private-mode repair, trailing-separator symlink rejection, nonblocking
  rejection of a profile-named FIFO, and no recursive deletion;
- exported symbol allowlist;
- process mode without link-time ORT dependency.

Use fault injection for allocation, loader, and ORT error-plus-output failures.
The POSIX shim test build routes every wrapper-owned `malloc`, `calloc`,
`realloc`, aligned allocation, and returned `realpath` owner through one
internal fail-Nth seam that is absent from production activation. The
deterministic portable suite enumerates success-path allocations in
status/string, runtime canonicalization, provider, run-options/profiling,
session-options/metadata, two-entry external-data, tensor-owner, composite,
synchronous-run/result, cancellation-registration, data-lease, and tensor-info
operations. The status-specific matrix distinguishes status-object OOM from
operation/message-copy OOM: only the first uses the static emergency status;
field-copy failure retains the original domain and numeric codes. Every failure
must leave its output neutral, release partial native owners, and permit a clean
retry. Opaque allocations owned by libc, the platform loader, or ORT itself
remain outside this wrapper fail-Nth seam. A separate provisioned exact-ORT
matrix covers dense, nonempty string, ZipMap sequence/map, optional Some/None,
string-element, and tensor-info paths, including recursive output validation.
The exact-runtime harness starts under hostile inherited fail-Nth variables and
must clear them before its first shim call, then controls each later fault with
its own monotonically changing epoch.
A fake `Run` hard-fails its next retry if a prior returned output owner leaked;
another fake returns an owned output together with an ORT error so the shim must
release it exactly once, preserve the ORT error, and complete a later run.
The provisioned native suite additionally profiles a real run with the exact
locked ORT, accepts only its closed timestamp basename shape, returns a
non-empty JSON event array, and proves the private artifact root is empty.
Compile separate Address/UndefinedBehavior and ThreadSanitizer variants where
toolchains/providers permit. The required Linux x64 TSan lane always runs the
fake-backed shared-value test; real-ORT concurrency remains an additional
integration gate when that exact runtime is provisioned. Strict native JSON
pre-decoders also run an exact empty-container depth regression and a
deterministic generated/mutated corpus whose accepted inputs must remain valid
for Dart's platform decoder.

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
Optional None types, prove the startup timeout covers a delayed isolate-spawn
future without resuming a late paused isolate, preserve that timeout across a
late spawn failure, preserve an exact worker-authored startup error through its
retirement acknowledgement, order uncaught error before exit on one lifecycle
port, retain active input bytes until that exit, use a valid ready port received
after malformed ownership only for acknowledged cleanup, reject exit without
the required cleanup receipt,
charge retained slots for empty string tensors in both per-message and
aggregate input bounds, include requested output names in the request-message
bound without inflating aggregate input reservations, retain a
malformed worker and its input reservation until gated run-state cleanup
completes, require controller acknowledgement before worker-authored fatal
protocol/cleanup replies may exit, reject mismatched fatal request IDs instead
of treating them as stale results, gate valid result/error publication behind
run-state disposal, exercise aggregate input-byte exhaustion/release and
byte-aware pool routing, and surface terminal pool failures before
backpressure.

One bounded composed-trace regression derives eight unique traces from the
fixed seeds `5`, `7`, `1`, `3`, `17`, `2`, `15`, and `4`. Explicit worker
gates, not elapsed-time choices, cover both queued positions for graceful
close, both queued positions for crash, and all four queued-position by
success/error orientations for a two-worker pool. A trace owns at most two
workers, four submitted runs, nine recorded actions, and 52 aggregate reserved
input bytes. Every trace rejects overfill, exercises queued and active
cancellation, awaits every result and close future, and ends with zero Dart
run/input reservations, closed sessions, and one controller connection-close
initiation per worker. The same test admits request IDs
`INT64_MAX - 1` and `INT64_MAX` once, proves permanent zero-capacity
exhaustion, and verifies that an exhausted peer does not turn a live saturated
peer's backpressure into a closed-pool result, while an undersized live peer
cannot make an impossible route retriable. Its two-second waits are
deadlock watchdogs; trace assertions and gate watchdogs report the seed and
action trace, and cleanup releases any held synthetic gate before awaiting
close. This synthetic harness does not claim native-owner leak freedom, target
execution, throughput, or RSS.

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

The Apple auditor defaults to the exact Fonix reference-app inventory. A real
consumer app with additional native frameworks selects
`--ios-application-profile consumer`; that profile still inventories every
Mach-O and signature, requires the declared, plist, Runner, hook, and packaged
Fonix identities to agree, and rejects a separately packaged or dynamically
linked ONNX Runtime.

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

`tool/ci/run_ios_reference_app_gate.py` takes one more bounded external copy,
freezes its source epoch, and derives linked arm64 device and simulator variants
from the same tree. It requires the exact lock-selected iOS NuGet and macOS host
archives, macOS 26.5.2 (25F84), Xcode 26.6 (17F113), both iOS 26.5 SDKs, and
Flutter revision `bd1e75d918605c91b411e8789fb911e6c9a84534` (Flutter
`3.47.0-0.1.pre`). The app contract is iOS 15.1; exact audited binary floors
remain 15.1 for Runner and the shim and 15.0 for App and Flutter.

The device branch builds Release without code signing and applies the closed
development policy: the root app/executable are unsigned with no provisioning
profile, while the three nested frameworks have exact teamless ad-hoc
signatures. This is static-only final-application evidence, not physical-device
execution. The simulator branch builds Debug twice and submits the exact
audited second tree to `simctl`. The installed identity must preserve every
path and byte and may differ only by the three exact framework executable-bit
clearings in the closed transport normalization. On the selected iOS 26.5
arm64-capable iPhone 17 Pro simulator, the application delegate validates the
exact smoke/challenge process-environment pair and exposes only its cached
null-or-closed activation over an argument-free app-owned channel; Dart
revalidates it within five seconds and never receives the raw environment. The
gate then binds a resident receipt to a fresh 256-bit challenge and PID. It
requires ORT 1.27.1 linked wrapper-owned CPU, output
`[1,4,9,16,25,36]`, full assignment, double close, process settlement, and
uninstall.

The frozen source checkpoint passed all 72 application tests and analysis;
`reference_smoke_test.dart` passed 25/25 and
`ios_project_contract_test.dart` passed 5/5. Two consecutive linked simulator
Debug builds passed. The complete iOS gate result is
`PASS (2026-08-07)`, bound to manifest SHA-256
`9c790a572f9876262496ffc47f85e2cbc4c027a9e3846cdd43c5d5489a14f23a`
and closed source-tree SHA-256
`2c275c655d3569ec767f36d9e99522b5e38ba937e6236666abe0795ea43a03a2`.
Its final-bundle audit excludes a separately
packaged raw ORT Mach-O and an audited ORT load-command dependency and binds the
packaged shim to the exact prepackage hook output through normalized fields,
hook metadata, schema-3 identity, dyld exports/fixups, and accounted
transformations. It does not prove the absence of runtime `dlopen`, another
static ORT copy in a different Mach-O, or exactly-one static archive linkage.
It also does not prove physical-device/signing/distribution, iOS
CoreML/XNNPACK/GPU/Neural Engine qualification, performance, simulator Release,
or transferability to another tuple.

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
native inputs pass the static profile and 16 KiB checks. The corrected exact
arm64-v8a APK now also has all four trusted target captures and a passing
schema-2 aggregate described below.

The corrected checkpoint is bound to commit
`8a9b6812c17237aeab7ec6d933f23932668c4b33` and source-manifest SHA-256
`3728b31a38c246328cbeda7af14a7ba502489f6e89046371d2c22d29f264efd2`.
The Android sherpa gate, executed on a macOS arm64 host, statically audited all
eight assets and the single-ORT graph in a 45,070,602-byte API 35 arm64-v8a
release-minified APK
(`35cca3502504b07d2d21fd27cb46fd5833fab9c6337115adfd5a309818a28ac3`)
and 26,045,592-byte base-only AAB
(`ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`).
The static package manifest and report hashes are
`99ccfb2f88eb16b9a7fdd3e03edf39c1529f771dedf210f5e481d85b3afd60e9`
and
`6ad082a3cdf3a498f88504e24a47eb6e239a0d62f13ea02e11b7fbe202e4764e`.

The exact same APK and harness contract
(`5460d723423515d519819e6e369646e6f6cd70edb89760c02da133da40cb7f23`)
then passed independent trusted-`adb` captures for both load orders on API 35
arm64 emulators with page sizes 4096 and 16384. Each run completed two cycles
and passed native Fonix cancellation, bounded between-frame sherpa cancellation,
stale suppression, recovery, both disposal orders, double disposal, and zero
pending work. The capture/receipt/validation hashes are:

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

Those four validation records passed the schema-2 compatibility aggregate with
SHA-256
`0eb7fece2610b0225060696aa8599a960d01955cc1a7bc6f2ba8d7596964f504`.
The capture manifests remain separate `trusted-adb-capture` evidence; each
validator and the aggregate remains `offline-consistency-only`. This proves
only the exact API 35 arm64-v8a release-minified APK matrix. API 24,
physical-device, AAB-derived-install, other ABI/build, performance, signing,
distribution, and general Android support remain unproved.

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

### Public-API CPU benchmark V1, collector, and replay validator

Implementation checkpoint (2026-08-09): the committed Flutter reference app
contains a macOS/Linux-only, exact `FONIX_CPU_BENCHMARK=1` activation over a
generated static-weight float32 MatMul. The model computes
`[2048, 1024] @ [1024, 1024] -> [2048, 1024]`; its model, input, reference
output, metadata, generator, shapes, operation count, and exact-byte output
policy are independently reproducible and SHA-256 bound.

Formal V1 contains both the serial and bounded-pool measurement contracts. The
serial phase uses the synchronous public API with pool size and concurrency
one, sequential execution, graph optimization `all`, explicit intra/inter-op
thread counts of one, CPU arena and memory patterns enabled, and deterministic
compute. It pre-creates one reusable input tensor, measures runtime load,
session creation, input materialization, first inference, and native-to-Dart
output copy separately, requires three consecutive batch-median changes within
10 percent under a 100-run bound, records 100 warm samples, and completes three
fixed one-second throughput windows. Every throughput cycle includes inference,
output copy, exact-bit validation, and result disposal. Total-process
current/peak RSS is sampled at eight phases; strict full CPU assignment uses a
separate profiled session after serial timing.

The pool phase measures the public `OrtSessionPool` surface with exactly two
protocol-v4 workers and concurrency two. It first copies the 8 MiB fixture into
an immutable isolate tensor. Each worker accepts at most one run, has a 32 MiB
message bound and a 16 MiB aggregate input bound, and admits one exact
8,388,629-byte reservation containing the fixture plus the closed request
accounting. A first concurrent round starts both runs before awaiting either
and must observe two outstanding runs and both reservations. Concurrent round
duration then uses the same five-sample, three-transition, 10-percent
stabilization rule under a 100-round bound.

Each of three one-second pool windows runs two controller lanes through the
full isolate round trip: controller input copy, worker decode and native tensor
creation, inference, worker output copy and transfer, controller decode, Dart
float32 output copy, and exact-bit validation. A lane stops admitting work
after the monotonic target is reached; both lanes then drain, and the fragment
records per-lane and total completions, duration, and observed concurrency.
Seven ordered total-process RSS samples cover input preparation, pool startup,
the first concurrent round, stabilization, throughput, pool close, and
assignment evidence.

The timed pool must have zero outstanding runs and input bytes before and after
an idempotent double close. After all pool timing, a different strict two-worker
pool admits two simultaneous profiled runs so each worker returns one full-CPU
assignment receipt. It also drains, closes idempotently, and removes every
private profile artifact. The target publishes only after both the serial
phase and bounded pool phase settle. Its one path-free line begins
with `FONIX_CPU_BENCHMARK_FRAGMENT=`, has exact purpose
`measurement-only-target-fragment`, uses target-fragment V1, and repeats
the challenge and positive target-process ID. A cold-runtime sample remains
meaningful only when a host collector launches a fresh final-application
process.

`tool/ci/collect_cpu_benchmark.py` implements that host boundary for macOS
arm64 and Linux x86_64. It launches exactly five fresh direct children, binds
each fragment to a distinct challenge and the observed PID, records bounded
device, OS, driver/firmware, power, thermal, total-process RSS, and process CPU
observations, then publishes only after every process and identity check
settles. Its exact output inventory is five raw `fragment-NN.json` files,
`host-observations.json`, and `cpu-benchmark-collection.json`. The host sidecar
is raw schema-1 measurement input; the derived serial-and-pool collection is V1
and identifies the closed collector contract as
`fonix-cpu-benchmark-collector-v1`.

The pinned Flutter 3.47 macOS Release bootstrap has one closed stderr
allowance, followed by one LF:

```text
[IMPORTANT:flutter/shell/platform/embedder/embedder_surface_metal_impeller.mm(53)] Using the Impeller rendering backend (MetalSDF).
```

The line is required exactly; a missing, changed, prefixed, or suffixed line
fails collection. Linux requires empty stderr until exact target-host evidence
adopts a different closed contract.

The collector requires the executable, shim, runtime, resolver manifest, and
every provider dependency to be exact regular-file members of one canonical
measured application tree. It also binds the native lock, resolver and embedded
build manifests, package/runtime/API identities, protocol/schema/tool bytes,
and the closed repository source manifest. The tree is observed unchanged
around the launches, and the target-reported runtime basename must select one
unique packaged member. The shim reports its embedded build contract. These
checks do not independently prove that the exact supplied shim, runtime, or
provider bytes were loaded; that stronger claim requires the platform loader
audit. The separately recorded source tree is not compiled-source provenance,
packaged runtime equivalence to an upstream archive still requires the platform
audit, and no distribution archive is claimed.

Run collection and independent replay with the current command boundaries:

```bash
python3 -B tool/ci/collect_cpu_benchmark.py \
  --repository /absolute/path/to/fonix \
  --application-root /absolute/path/to/final-application-tree \
  --executable /absolute/path/to/final-executable \
  --shim-artifact /absolute/path/to/packaged-fonix-shim \
  --runtime-artifact /absolute/path/to/packaged-onnxruntime \
  --resolver-manifest /absolute/path/to/packaged-resolver-manifest \
  --output-directory /absolute/new/cpu-benchmark-collection

python3 -B tool/ci/validate_cpu_benchmark_collection.py \
  --collection-directory /absolute/cpu-benchmark-collection \
  --repository /absolute/path/to/fonix \
  --application-root /absolute/path/to/final-application-tree \
  --executable /absolute/path/to/final-executable \
  --shim-artifact /absolute/path/to/packaged-fonix-shim \
  --runtime-artifact /absolute/path/to/packaged-onnxruntime \
  --resolver-manifest /absolute/path/to/packaged-resolver-manifest \
  --output /absolute/new/cpu-benchmark-validation.json
```

Repeat `--provider-dependency` for each packaged provider dependency; an exact
Linux Xvfb display may be supplied with `--display` to the collector. Both
output locations must not exist. The offline validator reopens the exact
seven-file inventory, validates the closed schemas and identities, and
independently rederives the complete collection from the raw fragments and
host sidecar. Its output is validation-record V1 with
`claimStatus: measurement-only` and
`validationScope: offline-consistency-only`. It is distinct from collection
V1 and from the older generic receipt and
`validate_benchmark_receipt.py` flow above.

The active contract contains exactly four V1 templates: the protocol descriptor,
target-fragment schema, collection schema, and validation schema. Protocol,
fragment, collection, validation, and collector identities all use V1. Replay
registers exactly three schemas and follows one closed reference chain:
validation to collection to the self-contained target schema. Every file
identity is pinned and rechecked around replay. There is no legacy schema
registry, alias, or compatibility branch.

Before this formal contract, the repository contained an unreleased schema-1
serial fragment prototype. Two later checkpoints were mistakenly assigned
higher version numbers while the package was still unpublished. Those bytes
remain only in Git history and are not active protocols, accepted artifact
formats, evidence, or compatibility obligations.

After either command reserves its final output, it never unlinks or recursively
cleans that output on a publication or durability failure. An incomplete
collection directory or validation file is invalid evidence and intentionally
blocks automatic retry; retain it for diagnosis or remove it explicitly before
a deliberate retry. This fail-safe rule avoids deleting a concurrently
substituted same-user path.

The collection derives one closed comparability status. Only
`baseline-comparable`, meaning complete and stable recorded power and thermal
observations, is eligible to enter a later baseline or threshold review.
`incomplete` records missing observations, while `non-comparable` records
observed power-mode change or thermal drift. Both remain useful raw
measurements, but neither is eligible for comparison. The complete raw bundle
must be retained and independently reopened for every later evaluation; the
V1 validation record is not a substitute for those samples.

The macOS observer reads the public `NSProcessInfo` thermal-state enum and
dynamic low-power-mode boolean. It also invokes the absolute `/usr/bin/pmset`
through the bounded-process boundary, requires the same closed active source
before and after `-g custom`, parses the bounded configured-profile set, and
selects the nonempty profile matching that source. Profile headers are closed;
after stripping required leading indentation, each bounded setting contains
printable ASCII with horizontal tabs permitted. Trailing spaces or tabs, other
control bytes, and exact duplicate settings are rejected. The observer sorts
those exact remaining lines only to make the fingerprint order-independent. It
does not interpret any `pmset` key, including `lowpowermode` or `powermode`.
Evidence receives only a domain-separated SHA-256 profile digest together with
the closed source and current API-reported Low Power label; raw settings, paths,
and values remain withheld. The API boolean and configured-profile fingerprint
are paired observations with no equality condition.

The four API-reported Apple thermal values are `nominal`, `fair`, `serious`,
and `critical`. Apple's `nominal` case can also mean the current thermal state
could not be determined, so it is not a positive thermal-health attestation.
Foundation likewise reports Low Power `off` when the state is unknown or
unsupported, so that label is API-reported rather than a positive attestation
of support or a disabled state. A stable elevated thermal value may be
matchable across the five launches but still requires explicit review.
`baseline-comparable` means only that all ten surrounding observations were
available and stable enough to serve as matchable input to a later baseline
or threshold review. It does not approve a baseline. Missing APIs or
malformed, ambiguous, or source-drifting `pmset` output remain
`incomplete`; observed changes remain `non-comparable`. Linux continues to
expose its bounded CPU-governor label but reports thermal state as unavailable.

Focused formal-V1 Python coverage passes 104/104 cases: 31 derivation/core, 41
collector, and 32 independent replay-validator tests. These cases cover the
three-schema reference graph, rejection of non-V1 identities, raw preservation,
process-lifetime peak RSS, two-worker assignment parity, authentic second
admission, occupancy-drain settlement, environment derivation, and hostile
publication/replay inputs. The complete Python suite passes 829/829, the
current Flutter SDK root suite passes 226 tests with 54 explicit
environment-gated skips, and a clean external-copy reference app passes
analysis and 90/90 tests, including all 18 formal-V1 benchmark cases. The exact
ORT 1.27.1 two-worker integration passes 1/1.

Formal target checkpoint (2026-08-09): the collection used the macOS arm64
Release reference application and the source snapshot at commit
`7df0eee5bd191f0f6ee0f0e29b6ebab5c41ff8fc`, whose 49,397-byte source manifest
has SHA-256
`afe8536f7ad019ceaf9cd362a17ae68d84ef944411239cdc9d6ca165083b4eb5`.
The collector completed five fresh challenge/PID-bound launches and bound one
66,704,118-byte canonical application tree with SHA-256
`efe4719d8fa2f3a2fe1f59edb0df146519cf7968635ac30ec129f33da886ae5f`.
It emitted a 182,071-byte collection with SHA-256
`139673b4a24c3ce5cb962b39b1180697b1ab1c9a9b619e03bad70ad8e1f20866`.
The complete seven-file, 238,837-byte raw bundle has SHA-256
`976a0d5fb6dc7cafa69d7ac50f518200a520cae7996b7d68a59d414202ce3a93`.
Independent replay validated that bundle and emitted a 9,763-byte record with
SHA-256 `d19167221f3de677f5f15f05d7d829d3b4fe6a50095f244a4005cc26b5f674bc`.
The raw bundle and validation record currently exist only at
`/private/tmp/fonix-cpu-v1-collection-20260809-a` and
`/private/tmp/fonix-cpu-v1-validation-20260809-a.json`. `/private/tmp` is not
durable evidence storage. No future retention or replay-availability claim may
rely on these local files until an approved durable external store receives the
exact bytes and their hashes are reverified.

The exact macOS 26.5.2 arm64, bundled ORT 1.27.1 CPU tuple recorded serial warm
inference p50/p95/p99 of 2,269/2,318/2,410 microseconds across 500 samples.
Its 15 serial windows completed 3,131 runs at an aggregate 208.239 runs/s;
window p50/p95/p99 were 208.039/209.488/209.488 runs/s. The two-worker
full-isolate-roundtrip phase completed 2,082 runs across 15 windows at an
aggregate 137.573 runs/s; window p50/p95/p99 were
139.502/143.273/143.273 runs/s. Serial and pool peak total-process RSS maxima
were 261,062,656 and 490,176,512 bytes. Both strict pool assignment receipts
and the serial assignment receipt reported full CPU assignment with no
fallback; zero-accounting and idempotent-close checks passed.

The host observations were stable `nominal` and AC-power Low Power `off`, so
the collection reports `baseline-comparable`. That status only makes these raw
measurements eligible for a later baseline review. Apple can report `nominal`
or Low Power `off` when state is undetermined or unsupported. The fragments
and host observations are not independently authenticated, the supplied native
members do not prove their loaded bytes, the source manifest is not compiled
provenance, and no distribution archive is claimed. The replay remains
`measurement-only` and `offline-consistency-only`. Earlier prototype bundles
remain non-V1 historical evidence. This exact result establishes no baseline,
threshold, performance guarantee, provider qualification, platform-support
promotion, release approval, Linux result, distribution artifact, or
cross-target evidence.

## 8.12 CI matrix

### Current required workflow

The checked-in pull-request/push workflow currently runs:

- Dart format, analysis, and ordinary tests on the current minimum and stable
  SDKs; these pure-Dart jobs resolve only the root package, while the separate
  platform application gates resolve `example/` with their selected Flutter
  toolchain;
- deterministic reconstruction of the reviewed public Dart API and native C
  ABI records on the minimum SDK, followed by isolated binding regeneration
  and an exact diff;
- Python CI-script and standalone verifier tests, the C source-quality gate,
  release-evidence checks, and the closed source checksum;
- deterministic Phase-3 fixture-byte checks on Linux, macOS, and Windows;
- warning-as-error native CTests on Linux x64 and macOS arm64 with the
  documented Address/UndefinedBehavior sanitizers, a separate Linux x64 TSan
  lane, plus the Windows x64 shim/security contract without real ORT inference;
  and
- the exact ORT 1.27.1 macOS arm64 runtime, native bridge, worker lifecycle,
  Dart value/provider profile, build-hook, freshly generated final Flutter
  application gate, and committed public-API reference-application gate.

The POSIX native and Phase-3 runners, Linux `readelf`, desktop `objdump`,
binding verifier, macOS runtime checker, Apple final-application auditor, and
macOS/iOS application gates use the shared `tool/ci/bounded_process.py`
boundary. It is designed for trusted CI commands: stdin is `/dev/null`; each
command starts a new POSIX session and process group; stdout and stderr are
independently capped and incrementally validated as strict UTF-8; and a
monotonic wall deadline is followed by bounded process-group TERM, KILL, and
direct-child reap steps. A direct child that exits while an ordinary inherited
group member or output pipe remains is rejected. The boundary owns only the
spawned process group inherited by ordinary tool subprocesses. It is not a
sandbox, and deliberate `setsid` or `setpgid` escape is outside its contract.

On Darwin, XNU can retain a process-group record with no signalable non-zombie
member briefly after direct-child exit and report `EPERM` instead of `ESRCH`.
Only this post-exit permission state receives a 50-millisecond monotonic
settlement window; output continues draining at one-millisecond rechecks, and
success requires a later `ESRCH`. Persistent permission failure or a transition
to a signalable numeric group fails closed. Because post-reap permission made
that number's ownership ambiguous, the failure path drains and reaps without
signaling the possibly reused group ID.

The native and Phase-3 configure, build, and inventory deadlines are five,
twenty, and two minutes respectively; the Phase-3 byte check is also limited to
two minutes. General command output is limited to 16 MiB per stream and CTest
inventory output to 4 MiB per stream. CTest receives `--timeout 300` for each
test and has a separate 1,800-second outer suite deadline. Linux-auditor
`readelf` calls use the same POSIX helper with a 30-second deadline and 8 MiB
per stream, and successful invocations still reject stderr. Focused helper
tests cover independent stream overflow, invalid and split UTF-8, timeout,
TERM-ignoring residual group members, nonzero exit, launch failure, cleanup,
and the explicit non-POSIX contract.

Binding regeneration uses 15-minute generation and five-minute formatting
deadlines with 16 MiB per stream. Its POSIX environment removes every
`DYLD_*` and `LD_*` key, selects the `C` locale, and suppresses tool analytics.
POSIX desktop `objdump` uses a 30-second, 1 MiB combined report boundary and a
minimal `C`-locale environment. The macOS runtime checker bounds inspection,
compilation, and provider execution at two, five, and two minutes with 8 MiB
per stream and accepts only validated regular executables at the exact
`/usr/bin/cc`, `codesign`, `nm`, and `otool` entry points. Those commands
receive only `PATH=/usr/bin:/bin` plus the `C` locale, excluding inherited
Xcode, SDK, compiler, loader, cache, and search-path selectors. The checker
copies the locked versioned runtime once, without following links, into one
private regular-file snapshot named for the pinned `@rpath` install-name
basename. The copy is size/SHA-256 verified during and after staging, every
Mach-O/export/signature/provider operation uses that one snapshot, and it is
removed with the private directory. The Apple auditor gives each `otool`, `nm`,
`dyld_info`, `codesign`, compile, or probe call five minutes and 8 MiB per
stream. Apple audit and gate environments remove every `DYLD_*` key and select
the `C` locale.

The fresh macOS gate bounds version, create, offline resolution, asset
preparation, build, and audit-tool work separately; its longest build deadline
is 60 minutes and its command streams are capped at 16 MiB. The committed
macOS gate uses explicit two-to-sixty-minute operation deadlines, a 2 MiB
general cap, and a 64 KiB launch cap. The iOS gate uses explicit deadlines for
environment checks, source checks, asset preparation, builds, simulator
commands, boot settlement, and process probes, with 2 MiB general and 256 KiB
reference caps. Its bounded plist input is written to a private temporary file
and removed after conversion. Expected nonzero process probes are accepted
only when the owned group settled with no cleanup failure.

The macOS, iOS, Linux, and Android reference gates load their trusted
final-application auditors in-process rather than putting a second bounded
session owner around each auditor CLI. This prevents the auditors' individual
tool sessions from escaping an outer auditor process group while retaining the
same serialized-report byte boundaries and closed report validation. Dynamic
loading and the complete audit calls keep bytecode writes disabled, preserving
the former `python -B` source-epoch contract. The Android loader binds both the
auditor and its `android_gate_common.py` dependency to bounded regular files in
the selected repository and restores the caller's ambient module after either
success or failure.

On Windows, the native and Phase-3 runners retain direct-child deadlines; only
captured CTest inventory receives post-completion byte and UTF-8 checks.
Binding regeneration and desktop `objdump` retain explicit direct-child
fallbacks. They do not claim descendant-tree cleanup. Windows Job Object
ownership remains deferred until it is implemented and exercised on a Windows
host. None of these boundaries is an untrusted-code sandbox.

The Python job includes unit and tamper coverage for the Apple auditor, iOS
source-epoch/final-application gate, Android auditor, schema-2 load-order
receipt validator, schema-1 validation-record ingestion, four-record
compatibility aggregation, Android reference-gate orchestration, and the
trusted one-tuple target-runner contract. The complete iOS gate, full Gradle
package gates, and standalone Android CPU/XNNPACK emulator runs are explicitly
provisioned local evidence, not hosted CI lanes. The standalone and
sherpa-owned Android projects now carry separate strict SHA-256 Gradle
dependency-verification metadata for their exact macOS-hosted Release graphs.
Their controlled gates remove the named inherited JVM-option variables and
verification-specific Gradle project-property override before setting a
gate-owned strict system property. They assume a non-hostile local Gradle user
home and init-script environment. A separate disposable staged copy replays
each exact Release graph after cache provisioning with
`./gradlew --offline --no-daemon --dependency-verification strict assembleRelease bundleRelease`.
Gradle-wrapper/bootstrap resolution was not offline, the Flutter build
invocations are not claimed offline, and the current metadata selects macOS
AAPT2 only.

That checkpoint verifies Gradle/Maven input bytes; it does not make either
APK/AAB graph binary-reproducible. It also proves no signing/distribution
approval, Dart hosted-cache authentication, API 24 or physical-device run,
installed AAB-derived split, performance result, or QNN path. The sherpa
runner's four 4 KiB/16 KiB captures and aggregate remain local provisioned
evidence rather than hosted CI runs.

The inexpensive C source-quality lane is deliberately offline and
toolchain-independent. `tool/ci/check_c_source_quality.py` walks the closed
repository-owned C/H roots and rejects symlinks, inventory/size overflow,
non-UTF-8 or non-LF input, tabs, trailing whitespace, unbounded lines,
indented preprocessor directives, merge markers, and format/lint suppression
markers. The native CMake jobs remain the semantic compiler-lint gate through
`/W4 /WX` on MSVC and `-Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror`
elsewhere. This lexical gate does not claim `clang-tidy` static-analysis
coverage; adding a pinned cross-platform analyzer remains separate work.

The iOS final-application gate has the exact local result
`PASS (2026-08-07)`; it is not a hosted CI job, and its device branch is
not physical-device execution. The Linux x86_64 final-application gate,
independent ELF/tree/provenance auditor, reference scaffold, and focused
synthetic tests are implemented, but the gate has not passed on its exact
Ubuntu 18.04.6/glibc 2.27 host. Flutter Linux/Windows final-application smoke
and Windows/Linux target-host real-ORT inference are not present CI jobs. The
Android standalone CPU and XNNPACK gates have local API 35
arm64/4 KiB final-package evidence. The sherpa-owned path now also has local
runtime-provisioned release-minified APK/AAB static evidence, four trusted 4
KiB/16 KiB APK captures, and the complete exact-APK schema-2 aggregate. None of
these paths has a hosted job, API 24 runtime, physical arm64 run, x86_64 run, or
installed AAB-derived split; the standalone CPU/XNNPACK path also lacks a 16
KiB target run. The XNNPACK run is a functional emulator checkpoint, not performance,
thermal, or provider-qualification evidence. These remain target-evidence
requirements before promoting the corresponding support rows; a cross-build,
source test, static package audit, or tiny emulator fixture must not be
reported as a substitute.

The exact unsigned-device static-audit plus Debug-simulator arm64 CPU baseline
is now a standing regression. The next target-evidence step is executing and
calibrating the implemented Linux x86_64 gate on its exact host; local
stabilization/release work can proceed while that environment is provisioned.
Android aligned/QNN device work is deferred until
its exact licensed SDK/hardware tuple exists. All Windows
target-host, provider, final-app/package, installer, and clean-machine work is
deferred until a Windows environment exists. Their portable/static/source,
tamper, cross-build, and loader-security gates remain required.

### Future nightly/scheduled lanes outside the scoped CPU sequence

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

These are follow-up infrastructure candidates, not completion gates for the
current CPU-only scoped pre-1.0 work. The active rows remain macOS arm64
bundled CPU, Linux x86_64 bundled CPU, iOS arm64 linked-device CPU, and Android
arm64 application-owned bundled plus sherpa-owned process CPU: four OS target
rows and five compositions.

Deferred lanes are Android QNN device qualification and Windows x64/arm64
target-host, DirectML, final-package, installer, and clean-machine runs. Keep
their source/static/security jobs green without scheduling them as the next
development slice.

### Scoped pre-1.0 release and future global release

The planned scoped pre-1.0 release requires:

- every exact target package and sample app selected for advertising;
- complete provider qualification for only the flavors selected for
  advertising;
- the exact sherpa compatibility matrix for each advertised Android
  composition;
- final artifact inspection for every advertised package type;
- SBOM/notices/checksums/signing and the independently owned licensing and
  security approvals;
- clean-machine install/run tests for every advertised target; and
- a reproducibility record.

Android QNN and every Windows target-host, provider, final-package, installer,
and clean-machine lane remain excluded and explicitly unsupported in that
scope. Their portable/static/source/tamper/cross-build/security regressions stay
mandatory. An unqualified `Release-ready` or 1.0 claim additionally requires
all Tier-1 packages and sample apps, including the deferred Windows lane, and
every other five-platform release gate.

The API/ABI baseline checks are shared portable regressions, so both remain
mandatory even though Windows execution and Android QNN qualification are
deferred. They expose declaration drift for review but do not satisfy the
detached API/ABI approval or any target-execution record.

## 8.13 Suggested CI job names

```text
lint-dart
bindings-regenerate
native-linux-x64-asan
native-linux-x64-tsan
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
