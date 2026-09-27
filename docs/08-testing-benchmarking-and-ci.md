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

For the separately qualified SDK 27.0 shim, select `consumer-ios27` explicitly.
Its [qualification and limits](evidence/2026-09-28-ios27-consumer.md) include
the exact SDK/linker metadata, strict Simulator audit and CPU receipt, and
static-only unsigned consumer device audit. It does not update or relax the
historical reference gate's toolchain pins.

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

- fixed tracked model and inputs;
- fixed provider options;
- fixed thread counts and power mode;
- same precision/batch/shape;
- CPU reference run;
- record fallback/assignment;
- isolate cold and warm cache cases;
- randomize/interleave provider order where thermal drift matters.

### Ordinary desktop benchmark

The committed reference app accepts `FONIX_CPU_BENCHMARK=1` on macOS and Linux. It performs three warm-up runs and ten measured synchronous CPU inferences, includes output materialization in each sample, checks every output against the tracked float32 reference, and prints the raw microsecond samples with min, median, average, and max.

This is a convenient local measurement, not a receipt protocol. Record the exact device, OS, runtime artifact, build mode, thread settings, and power or thermal state alongside results that will be compared. Provider qualification still needs its normal assignment, parity, fallback, and target-device evidence.

## 8.12 CI matrix

### Current required workflow

The checked-in pull-request/push workflow currently runs:

- Dart format, analysis, and ordinary tests on the current minimum and stable
  SDKs; these pure-Dart jobs resolve only the root package, while the separate
  platform application gates resolve `example/` with their selected Flutter
  toolchain;
- reconstruction of the reviewed native C ABI record on the minimum SDK,
  followed by isolated binding regeneration and an exact diff;
- Python CI-script and standalone verifier tests plus the C source-quality
  gate;
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
- licenses, notices, release-artifact checksums, signing, and the normal
  security review for the chosen distribution channel;
- clean-machine install/run tests for every advertised target; and
- a reproducibility record.

Android QNN and every Windows target-host, provider, final-package, installer,
and clean-machine lane remain excluded and explicitly unsupported in that
scope. Their portable/static/source/tamper/cross-build/security regressions stay
mandatory. An unqualified `Release-ready` or 1.0 claim additionally requires
all Tier-1 packages and sample apps, including the deferred Windows lane, and
every other five-platform release gate.

The native ABI baseline check is a shared portable regression even though
Windows execution and Android QNN qualification are deferred. Dart API changes
are reviewed through analysis, tests, the code diff, and the reference app.

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
