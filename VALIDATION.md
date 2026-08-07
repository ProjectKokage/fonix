# Validation Report

- Validation date: 2026-08-07
- Package: `fonix 0.1.0-dev.1`
- Host: macOS 26.5.2 (25F84), arm64

## Result

The declared development API and locally controllable package boundary pass
the completed validation recorded below. The current shared reference source
passes 72/72 application tests and analysis. The earlier manifest-bound iOS
gate source epoch passed 68/68 application tests, including 24/24
resident-publication tests and 5/5 iOS project-contract tests, and its linked
simulator application passed two consecutive Debug builds. The complete iOS
gate result remains `PASS (2026-08-07)` for that exact frozen epoch.

This snapshot is not implementation-complete. The Linux x86_64 source path is
implemented but has no exact target-host PASS. Local stabilization now uses an
ordered protocol-v4 worker retirement handshake after malformed replies and
worker-authored terminal failures, and includes `Isolate.spawn` inside the
startup deadline. Sessions and pools now
reserve aggregate input bytes across active and queued work. The native and
standalone Phase-3 runners, Linux `readelf`, POSIX desktop `objdump`, binding
regeneration, macOS runtime inspection, Apple final-application auditor, and
macOS/iOS reference and final-app command paths now share a bounded
trusted-command boundary. It applies independent stdout/stderr byte caps,
incremental strict UTF-8 validation, monotonic deadlines, bounded
process-group TERM/KILL, and direct-child reap cleanup. A successful
direct-child exit is rejected while the new POSIX process group inherited by
ordinary tool subprocesses or an inherited output pipe remains. This is not a
sandbox: deliberate `setsid`/`setpgid` escape is outside the contract. Native
and Phase-3 CTest calls have an exact 300-second per-test timeout inside a
1,800-second suite deadline.

The Apple, Linux, and Android reference gates call their final-application
auditors in-process so the auditors' bounded tool groups are not nested beneath
an outer session owner. Their former closed serialized-report boundaries are
preserved, and bytecode writes remain disabled across the dynamically loaded
audits. The Android gate binds both the auditor and its shared helper to the
selected repository rather than reusing an ambient module. Apple audit and
gate commands scrub `DYLD_*` loader injection and use a
deterministic locale; binding tools also scrub `LD_*`, and the POSIX desktop
metadata auditor uses a minimal fixed environment. Expected nonzero status
probes are accepted only after clean group settlement, and the iOS plist
converter stages its bounded input in a private temporary file that is removed
after the call. The macOS runtime checker validates exact regular `/usr/bin`
entry points and invokes them with only a fixed system `PATH` and `C` locale,
so caller-supplied Xcode, SDK, compiler, and loader selectors are absent. It
copies the locked runtime once into a private, reverified regular-file snapshot
matching its `@rpath` install-name basename; every Mach-O, export, signature,
and provider check uses that same snapshot.

Windows retains a direct-child timeout fallback and post-completion CTest
inventory bounds; binding regeneration and desktop `objdump` also retain
direct-child Windows fallbacks. Job Object ownership and descendant-tree
cleanup remain deferred and unclaimed. This is not a claim that every
repository subprocess is an untrusted-code sandbox.

The macOS gate applications and independently audited Android arm64-v8a
Release APKs load and execute their exact packaged CPU paths. A separate
Android profile build also passes the closed XNNPACK functional
assignment/parity/fallback/recovery path on the named API 35 arm64/4096-byte
emulator. The source-final sherpa-owned APK passes the complete trusted
four-record matrix: both load orders on named API 35 arm64-v8a/4 KiB and 16 KiB
emulators, with runtime negotiation, two alternating cycles, cancellation,
stale suppression, recovery, both disposal orders, and cleanup bound to the
installed APK.

This is not redistribution, five-platform, unqualified `Release-ready`, or 1.0
approval, transfer to an untested Android tuple, or XNNPACK provider
qualification. The package remains `publish_to: none`, and the external
licensing, signing, target-host, physical-device, performance, and provider-
qualification boundaries listed below remain authoritative.

## Implemented boundary

The checked source contains the complete app-owned wrapper layer described by
the active documentation:

- a stable 67-export C ABI over ONNX Runtime API 27 with closed status,
  runtime, session, value, composite-value, run, cancellation, profiling, and
  data-lease ownership;
- a typed Dart API for trusted runtime sources, file/byte/external-data models,
  dense tensors, strings, float16/bfloat16 storage, sequences, maps, optionals,
  copied metadata, native-backed leases, and named synchronous inference;
- bounded worker-isolate sessions and pools using wire protocol version 4,
  paused-spawn parent ownership, early authoritative command-port transfer,
  correlated readiness, cooperative cancellation, graceful timeout cleanup,
  stale-result suppression, backpressure, and idempotent shutdown;
- ordered CPU, XNNPACK, CoreML, legacy NNAPI, QNN, CUDA, TensorRT, DirectML,
  OpenVINO, oneDNN, and MIGraphX configuration with discovery, registration,
  per-run assignment evidence, fallback enforcement, and scoped CoreML cache
  identity; and
- five-OS build-hook paths, exact offline artifact resolution, final-package
  auditors, Android single-ORT ownership and sherpa/QNN evidence tooling,
  deterministic fixtures, benchmark-receipt validation, and closed source and
  release-evidence checks;
- a shared bounded-command helper for trusted POSIX native, Phase-3,
  Linux/desktop audit, binding, macOS runtime, and Apple application/gate
  commands, with online per-stream caps, strict UTF-8, monotonic deadlines,
  process-group TERM/KILL, direct-child reap cleanup, and residual-group or
  output-pipe rejection; deliberate process-group escape and Windows Job
  Object ownership remain outside that helper's contract;
- a committed iOS arm64, macOS arm64, Android arm64-v8a, and Linux x86_64
  Flutter reference app over the public Fonix library, with bounded worker
  ownership, exact
  model/runtime identity, cancellation, retry, lifecycle replacement,
  stale-result suppression, deterministic cleanup, and platform-specific
  final-package gates; the Linux source adds a relocatable `$ORIGIN/lib`
  layout, closed GNU-versioned exports, and a strict final-tree/ELF/provenance
  auditor, but has no target-host PASS. The iOS path uses linked runtime
  ownership and a resident, challenge/PID-bound receipt. Its app-owned native
  launch channel
  exposes only a cached null-or-closed activation to Dart, which revalidates it
  with a bounded wait and never receives the raw process environment. The
  Android one-shot mode has separate exact CPU and XNNPACK profiles, with the
  latter owning strict
  assignment, exact CPU parity, explicit fallback report/rejection,
  post-rejection recovery, and deterministic five-session cleanup; and
- a separate sherpa-owned Android arm64-v8a reference scaffold with
  deterministic bounded Fonix/VAD fixtures, real process-runtime Fonix and
  sherpa adapters, one authoritative lifecycle publisher, active native
  cancellation and stale-result suppression, recovery and disposal checks,
  and a trusted UID/PID-bound one-tuple target runner. Its runtime-provisioned
  staged gate binds the complete hosted dependency trees, generated plugin
  graph, all eight qualification assets, raw native inputs, and one exact R8
  Release APK/base-only-AAB static pair. That exact APK has separate trusted
  4 KiB and 16 KiB captures for both load orders and one aggregate
  compatibility manifest; the validator and aggregate records remain
  explicitly offline-consistency-only.

Sparse tensors, opaque/custom values, arbitrary provider plugins, training,
GenAI, and Web/WASM remain explicitly outside this API rather than unfinished
paths.

## Final verification summary

| Gate | Result |
| --- | --- |
| Dart formatting | Full source tree, 0 changes |
| Dart analysis | No issues with `--fatal-infos` |
| Ordinary offline Dart suite | 203 passed, 17 explicitly provisioned skips |
| Worker lifecycle with strict fake and exact ORT | 26/26 passed |
| Native FFI bridge with strict fake runtimes | 11/11 passed |
| Exact ORT core Dart inference | 8/8 passed |
| Exact ORT generated Phase-3 Dart corpus | 18/18 passed |
| Exact ORT CPU/CoreML run-evidence suite | 3/3 passed |
| Python CI-script tests | 585/585 passed |
| POSIX bounded-process helper | 14/14 focused tests passed; native, Phase-3, Linux/desktop audit, binding, macOS runtime, and application-gate integration contracts are included in the 585-test Python suite |
| macOS runtime checker focused tests | 10/10 passed; exact-ORT inspection also passed under a hostile parent selector environment |
| Linux final-app auditor/reference-gate focused tests | 39/39 passed; source-side synthetic coverage only |
| Android reference-gate focused tests | 37/37 passed; source-side orchestration coverage only |
| Apple final-application auditor focused tests | 54/54 passed |
| iOS reference-gate focused tests | 94/94 passed |
| macOS reference/final-app gate focused tests | 32/32 passed |
| Standalone Python verifier tests | 54/54 passed |
| C source quality | 34/34 files passed the closed byte/style gate |
| FFI binding regeneration | Both generated bindings reproduced exactly |
| Deterministic fixture inventory | 23 files and 23 closed model/data paths |
| Deterministic XNNPACK MatMul fixture | Generator check and exact-ORT packaged-asset identity/inference test passed |
| Exact ORT standalone fixture CTests | 2/2 passed |
| Native POSIX CTests with exact ORT | 14/14 passed |
| Native POSIX ASan+UBSan CTests with exact ORT | 14/14 passed; Apple LeakSanitizer unavailable |
| Adjacent/bundled-loader CTests | 5/5 passed, including Android arm64/x86_64 APK namespace paths and desktop regressions |
| Five-artifact offline audit | 8/8 passed |
| macOS/iOS build-hook suite with exact archives | 28/28 passed |
| Android API-24 build/audit suite | 4/4 passed |
| Linux x64/arm64 and Windows x64 Zig cross-build | 1/1 passed |
| Flutter asset publication | 7/7 passed |
| Fresh final macOS Flutter application gate | Passed, including packaged CPU inference |
| Committed macOS public-API reference application gate | 31 app tests, Release build, exact full-CPU receipt, double close, and independent final-app audit passed |
| Committed Android arm64 public-API reference gates | 40 app tests per clean profile build; development-signed R8 Release APK/AAB audits; unchanged CPU receipt; and closed XNNPACK assignment/parity/fallback/recovery receipt on the exact API 35 arm64/4096-byte emulator passed |
| Current shared public-API reference source | 72/72 app tests and analysis passed in a clean external copy |
| Committed iOS arm64 public-API reference gate | Frozen source epoch: 68 app tests and analysis; unsigned device Release static audit; two consecutive simulator Debug builds; install-transport-bound CPU/full-assignment receipt, process settlement, and uninstall: `PASS (2026-08-07)` |
| Source-final sherpa-owned Android arm64 package and 4 KiB/16 KiB target gates | 50 staged Flutter tests, analysis, locked offline resolution, exact eight-asset audit, R8 Release APK/base-only-AAB build, hosted-package/native-input guards, and closed package-pair audit passed; trusted `dart-first` and `sherpa-first` captures passed on the exact API 35 arm64-v8a/4096-byte and 16384-byte emulators, with verified uninstall after every run; the four-record compatibility manifest was generated |
| Closed source checksum manifest | Historical Android records retain their commit-bound manifest; the current iOS gate source-epoch binding is `PASS (2026-08-07)` |

The ordinary Dart skips are environment gates, not ignored failures. Their
real-runtime, fake-runtime, exact-archive, cross-build, and CoreML cases were
run separately above wherever this host had the required inputs.

## Exact runtime and fixture evidence

The real-runtime tests used the lock-pinned ONNX Runtime 1.27.1 macOS arm64
CPU archive with SHA-256
`e42b77a7281cc6e55141bf44fcfbac2c782b823a491bbb6ac33c781dd991f8a6`.
The generated Phase-3 manifest SHA-256 is
`8f0eb39e377a5308f46c6ae6264c223fc04e8b108eea3b257da57c196f09c759`.

The deterministic corpus covers scalar float64, symbolic Add and MatMul with
multiple concrete shapes, zero-length float32, bool normalization, every
wrapper-supported fixed-width integer type, strings, float16/bfloat16 raw
storage, copied metadata, unknown rank, sequences, string/int64 maps,
present/empty optionals, and explicit external data. The inventory verifier
allows only generator-owned `.onnx`/`.bin` paths plus the independently pinned
130-byte upstream `mul_1.onnx`; it rejects stale files, links, nonregular
entries, missing paths, and bounded-walk violations.

## Native and packaged evidence

Native compilation used Apple Clang 21.0.0, CMake 4.4.2, and warnings as
errors. The normal and ASan+UBSan suites each passed all 14 CTests against the
exact runtime; Apple Clang does not provide LeakSanitizer on this host. The
bundled loader passed adjacency, Flutter-framework, unexpected-layout, and
closed Android arm64/x86_64 APK-namespace tests, including malformed paths and
missing runtime/symbol failures.

The bounded-command changes were verified at the Python helper, auditor, and
runner-contract levels. The first native-runner slice was also exercised on a
macOS arm64 host through the POSIX native source harness, where configure,
build, inventory, and 12/12 CTests passed. Binding regeneration reproduced both
checked-in outputs exactly through the new command owner. The later Apple gate
migration has not rerun the full provisioned macOS or iOS application gates,
so their exact results above remain evidence for their named earlier source
epochs and artifacts rather than refreshed evidence for this process-control
change. None of these checks proves containment of a deliberately escaping
process or changes a platform support claim.

The hardened macOS runtime checker separately passed against the exact pinned
38,502,216-byte ORT dylib while the parent supplied hostile Xcode, SDK,
compiler, loader, and search-path selectors; the child tool environment omitted
them, and the single private snapshot passed byte, Mach-O, dependency, export,
provider, and embedded-signature checks. This is runtime-artifact inspection,
not a refreshed final-application gate.

The offline artifact cache contained the exact lock-selected macOS arm64,
iOS arm64 XCFramework, Android arm64-v8a/x86_64, Linux x86_64/arm64, and
Windows x64 archives. Static auditors rehashed source and staged bytes and
checked closed inventories, notices, architectures, dependencies/imports,
SONAME/install-name/RUNPATH policy, Android 16 KiB layout, provider adjacency,
and the shim's no-ORT-dependency contract where required.

Android shims were compiled at API 24 for arm64-v8a and x86_64 with NDK
30.0.14904198 (Clang 21.0.0) under both application-owned bundled and
sherpa-owned process modes. Zig 0.16.0 cross-built the Linux x64/arm64 glibc
2.27 and Windows x64 outputs, including the Windows `bcrypt` and `advapi32`
system-library contract. These are cross-build and static package-layout
results, not target-device execution claims.

The 2026-08-07 sherpa input audit used Kokage's exact locked federated
`sherpa_onnx` 1.13.4 Android package directories, not a synthetic JNI AAR. The
release is mapped to full source revision
`142807252687d81b40d6315f23470a1512a00de3`. The exact hosted archive
SHA-256 values accepted by the current receipt validator are:

- `sherpa_onnx`:
  `889c03cf7a8788795e3a6d35bf9f20b66d1862ea7a73a1b6aaf6e450715c870a`;
- `sherpa_onnx_android_armeabi`:
  `9e96729d99567c3f64fc6f4c232ef04b57ae4ef3bf1b21364ee6e461103cb7cf`;
- `sherpa_onnx_android_arm64`:
  `0337650bc2357f39b751f1b9ced37770de3b60026effe66473b557346b4e3f97`;
- `sherpa_onnx_android_x86`:
  `6eaa462a24bf881c8ee2b3ab5b9fad3d310b714fc9d3ac4b9f650d0a90c9d0ab`;
- `sherpa_onnx_android_x86_64`:
  `181aa0f0968adf2cf2dcb369c879ea372653864538b82772877e22748a80254f`.

These are hosted-package provenance values, not native-library inventory
digests. The exact audited native inputs were:

- arm64-v8a ORT `994848008526a934dfb579ac773b00e5867929234852b061005d45aacaee9533`,
  C API `cb0fe5f4d26e8f66a5466cfc760caafaf50c60128321e491f538d60857324f56`,
  and C++ API `f961acd4fc2582ed8bea395c941e8c7b51fd8b41cffb2855779103764d6e7247`;
- x86_64 ORT `7144a2015ce495677420b287cef908eb3d379e09913b5e715aa48826b2080f2d`,
  C API `7922e28904bd84f71932d81a3e44753041d7967ad3b2db1a68ec26cba6eee855`,
  and C++ API `e52ba5fdb4eb297381d51e8cc0e4dc616dd23b9812a285b63128f7284b91a2b6`.

Each ABI independently passed `sherpa-audit` with the explicit `flutter-ffi`
profile, exact C API/C++ API dependency graph, single ORT/libc++ ownership
checks, and static 16 KiB alignment. This proves the selected source native
inventories only. It does not prove which bytes Gradle selects, final-package
coexistence, Bionic load order, or alternating inference.

The accompanying schema-2 receipt validator and schema-2 compatibility
generator retain synthetic contract/tamper coverage. Four real raw receipts
now pass the validator, covering both load orders on independent 4 KiB and
16 KiB target runs, and they produce the required compatibility manifest. The
validation and aggregate records remain `offline-consistency-only` with
unverified target provenance: public tool hashes do not authenticate the
source of target or logcat JSON. The separately retained trusted-runner capture
manifests provide the target-capture provenance for all four exact tuples.

## macOS application and provider evidence

The final application gate used Xcode 26.6 (17F113) and Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` (Flutter 3.47.0-0.1.pre, Dart
3.13.0-282.1.beta). It created a fresh Release application at a macOS 14.0
floor, audited the packaged manifest/notices and arm64 Mach-O identities,
verified code signatures and loaded-section parity with the lock-selected
runtime, then loaded only through the packaged shim/framework layout and
produced the expected CPU outputs.

The separate committed-reference gate copied `example/` outside the package
checkout, reproduced the exact manifest/notices, passed 31 controller,
channel, smoke, and widget tests plus analysis, built Release, and verified
signatures, hardened runtime,
the sandbox, and the explicit local ad-hoc library-validation exception. The
independent final-application audit ran before the named Flutter executable was
launched directly. The packaged public-API path then reported ORT 1.27.1,
wrapper-owned bundled CPU,
the exact 130-byte model SHA-256, output `[1,4,9,16,25,36]`, active provider
`cpu`, full CPU assignment, and an idempotent double close. The gate removed
all `DYLD_*` overrides and bounded its copy, commands, output, receipt, and
runtime.

The same exact runtime produced validated one-run assignment evidence for CPU
and CoreML with `MLComputeUnits=CPUOnly`, output parity, scoped cache identity,
and worker-isolate transport. CoreML required normal host filesystem access to
create its separate OS-managed compilation workspace. This proves neither GPU
nor Apple Neural Engine execution.

## iOS arm64 reference application evidence

The iOS gate is pinned to macOS 26.5.2 (25F84), Xcode 26.6 (17F113), the
iPhoneOS and iPhoneSimulator 26.5 SDKs, and Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` (Flutter 3.47.0-0.1.pre). Its
offline cache contains the 135,152,698-byte
`microsoft.ml.onnxruntime.1.27.1.nupkg` archive with SHA-256
`9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383`,
plus the exact macOS host archive needed by host-side Dart commands. The gate
freezes the current closed source manifest into a private source epoch and
derives both application variants from that same tree.

The committed application and hook declare iOS 15.1 and arm64 only. Its four
closed Mach-O images are `Runner`, `App.framework/App`,
`Flutter.framework/Flutter`, and `fonix_shim.framework/fonix_shim`. The audit
binds the application identifier `dev.fonix.fonixReference` and
requires exact arm64 subtype, file type, header flags, platform, deployment
floor, dylib identity, dependency kind/path/version, load-command inventory,
RPATH, and shim nlist/dyld export profiles. `Runner` and the Fonix shim have a
15.1 load-command floor; the pinned Flutter `App` and `Flutter` frameworks have
their exact 15.0 floors beneath the application-level 15.1 contract.

The complete gate result is `PASS (2026-08-07)`. Its device branch
builds Release with `--no-codesign`. The root bundle and executable are
unsigned, contain no provisioning profile, and the three nested frameworks
carry exact verified teamless ad-hoc signatures. The result is `static-only`:
the application is not installed or executed on a physical device.

The simulator branch builds Debug twice to exercise the idempotent RPATH
normalization and audits the resulting arm64-only application with strict
development signatures. Before launch, its install-transport identity requires
the installed application to retain every directory, file path, byte, and
`Runner` executable bit from the audited tree. It permits only the exact
`simctl` normalization observed on this pinned tuple: executable `true` to
`false` for `App.framework/App`, `Flutter.framework/Flutter`, and
`fonix_shim.framework/fonix_shim`. On the iOS 26.5 arm64-capable iPhone 17 Pro
simulator, a fresh 256-bit challenge and exact launch PID bind the resident
receipt. On this pinned tuple, the native process received the `simctl` child
environment while Dart `Platform.environment` did not expose those launch
values; the application delegate therefore validates the exact two-value
activation and forwards only its cached closed result over an argument-free
app-owned channel. Dart revalidates that response within five seconds. The
public receipt reports ONNX Runtime 1.27.1, linked wrapper-owned
CPU, the exact 130-byte model, output `[1,4,9,16,25,36]`, active provider
`cpu`, full CPU assignment, and idempotent double close. The public report
retains only hashes of the launch challenge and PID. It then verifies process
settlement, install-transport ownership, uninstall, and package absence.

The final Mach-O audit proves that the closed bundle contains no separately
packaged raw Mach-O or audited load-command dependency attributable to ONNX
Runtime. Validated native-assets hook metadata, embedded schema-3 identity,
dyld exports/fixups, and normalized runtime fields bind the packaged shim to
the resolver-built prepackage output across the exact install-name,
local-symbol-strip, and code-sign/linkedit transformations. This does not prove
the absence of runtime `dlopen`, another static ONNX Runtime copy in a
different Mach-O, or that the selected static archive was linked exactly once.

The report preserves that boundary in `linkedRuntimeIdentity` through
`runtimeMode`, `packagedShim`, `referenceShim`, `hookInvocationMetadata`,
`normalizedRuntimeFields`, `normalizedRuntimeFieldsSha256`, `comparisonScope`,
`accountedTransformations`, `embeddedBuildIdentity`,
`nlistAndDyldExports`, `separatelyPackagedOrtMachOs`,
`auditedOrtLoadCommandDependencies`, `runtimeDlopenBehavior: not-proved`,
`otherMachOStaticOrtCopies: not-proved`,
`staticArchiveMultiplicity: not-provable-from-final-bundle`, and the explicit
`claimBoundary`.

This checkpoint produces no physical-device execution, provisioning, approved
device or distribution signing, IPA, App Store, or distribution evidence. It
also produces no iOS CoreML, XNNPACK, GPU, or Neural Engine assignment,
performance, memory, thermal, sustained-behavior, or provider-qualification
evidence. The simulator receipt is exact Debug-tuple evidence and does not
transfer to simulator Release or another device/runtime tuple.

## Android application evidence

The final standalone gate used Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` (Flutter 3.47.0-0.1.pre),
command-line tools 20.0, build-tools 36.0.0, NDK 28.2.13676358, OpenJDK
21.0.12, and bundletool 1.18.3. Flutter was revision-pinned; the Android and
Java toolchains were path/version-gated; bundletool and the ORT archive and
payload were size/SHA-256 pinned. Both offline `pub get` passes enforced and
preserved the copied lockfile, and the generated `local.properties` matched the
supplied Android SDK. The bundletool jar was 32,520,401 bytes with SHA-256
`a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29`.

The source archive was 135,152,698 bytes with SHA-256
`9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383`.
The closed packaged model inventory was:

- `mul_1.onnx`: 130 bytes, SHA-256
  `71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10`;
- `model.json`: 687 bytes, SHA-256
  `20ab7b1150a37516159c714abca3cb1cb6e48692da0c77f21c46e15336f71449`;
- `xnnpack_matmul.onnx`: 311 bytes, SHA-256
  `c75aaa93b0e1ae09e0bb12ddee5786c2fda803dfa5f565234e2b65e238623482`;
  and
- `xnnpack_matmul.json`: 1,298 bytes, SHA-256
  `76eb202b02211f34ee64ca6a88a2a24d9f58f334136fa7f1b7fcfe2e763f30ab`.

The packaged notice SHA-256 was
`fb0af774b4d7cffc5b9d046f2aaeade2f37df2f80abf8033c95dfffcc77a8866`.

Independent CPU and XNNPACK profile builds produced these audited arm64-v8a
artifacts:

- CPU APK: 42,767,029 bytes, SHA-256
  `47c190fe3d80832a7ba4a5847cee9dff6d2a2809a8dbac133d485b3b91009e0b`;
- CPU AAB: 25,594,073 bytes, SHA-256
  `dc404c3d5986aff503fa8ae4b988f0c42e14e8f0a12460b694abb3a2f068fc4b`;
- XNNPACK APK: 42,767,029 bytes, SHA-256
  `58103e45e493a416e570fe604cbcc631af4714ef7b47904e3d4921acf4b8e395`;
  and
- XNNPACK AAB: 25,594,265 bytes, SHA-256
  `15504fcbf4dcce1a9d21e0a7be1ca8933735923244792422d0dcdcbb8dc7a964`.

All four artifacts used the matching development-signing certificate SHA-256
`37b0508e9b25075e5c2831fb02927010cf19efccd204e018950aae4205aca1b3`,
packaged shim SHA-256
`054ec77ab4aa2520c77645c89a4dfd06f29f9878f6cbaa7a094bfb6d49007212`,
and packaged stripped ORT SHA-256
`dd5d6aa51f32f26c0c77d4a4cd5b62f5de80072369bf32a946a32608ee3f5774`.
The ORT loaded segments matched the 27,983,536-byte lock-selected payload with
SHA-256
`a7579e85ecc5465840d352c35f355e5b7418d36901670d36afd46555304458c2`.

Each artifact contained exactly four arm64 native libraries and the four
closed model/manifest assets, and passed the manifest, dependency, export,
notice, one-ORT, and static 16 KiB ELF checks. Both APKs passed
`zipalign -P 16`; both base-only AABs contained 83 archive entries, with 81
signed entries under the exact expected JAR-signature metadata inventory.

Each gate installed its exact audited APK on the named API 35 arm64 emulator,
verified a queried 4096-byte page size and installed `base.apk` hash equality,
and bound the structured receipt to the package UID. The CPU profile retained
its byte-for-byte receipt: ORT 1.27.1, application-owned bundled CPU, active
provider `cpu`, full assignment, output `[1,4,9,16,25,36]`, exact identities,
and double close.

The XNNPACK profile reported the same runtime identity and canonical
`intra_op_num_threads=1` configuration. Its strict CPU reference produced
`[7,10,15,22,23,34]`; six MatMul runs across two XNNPACK sessions each reported
one node assigned to `xnnpack` with exact CPU parity. The `mul_1` fallback
session reported one CPU node; the strict fallback session raised the closed
provider-evidence rejection with no output; a second XNNPACK cycle proved
recovery. The receipt bound five total sessions, idempotent double close, and
removal of every private profile root.

After each run, the app was force-stopped and uninstalled with verified
absence, and the gate verified that its owned emulator serial disappeared.
These are final-package functional emulator results only, not API 24, 16 KiB
runtime, physical-device, performance, thermal, AAB-split-install, x86_64,
sherpa-coexistence, QNN, or XNNPACK provider-qualification evidence.

## Android sherpa 4 KiB and 16 KiB target evidence

The bounded qualification path landed in `635e97d`; implementation commit
`8a9b6812c17237aeab7ec6d933f23932668c4b33` then corrected the source
inventory and made the committed-tree evidence reproducible. The source-final
`MANIFEST.sha256` is 36,887 bytes with SHA-256
`3728b31a38c246328cbeda7af14a7ba502489f6e89046371d2c22d29f264efd2`.
The runtime-provisioned gate copied the scaffold outside the checkout,
resolved the exact lock offline, verified all five hosted sherpa package trees
and generated four-plugin Android graph, passed 50 staged Flutter tests plus
analysis, and produced this arm64-v8a R8 Release package pair:

- APK: 45,070,602 bytes, SHA-256
  `35cca3502504b07d2d21fd27cb46fd5833fab9c6337115adfd5a309818a28ac3`;
- base-only AAB: 26,045,592 bytes, SHA-256
  `ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`.

The gate audited all eight qualification assets, the exact raw ORT, sherpa C
API, sherpa C++ API, and Fonix shim bytes, matching `libapp.so` and
`libflutter.so` loaded identities, the complete non-system dependency graph,
and static 16 KiB ELF compatibility. Its retained bindings are:

- 16,118-byte static package manifest:
  `99ccfb2f88eb16b9a7fdd3e03edf39c1529f771dedf210f5e481d85b3afd60e9`;
- 25,308-byte persisted static report:
  `6ad082a3cdf3a498f88504e24a47eb6e239a0d62f13ea02e11b7fbe202e4764e`;
- 1,552-byte target harness contract:
  `5460d723423515d519819e6e369646e6f6cd70edb89760c02da133da40cb7f23`;
- staged `pubspec.lock`:
  `c51a95db82b025011be6af9b93d02524bd10aae0cd9f051a1a86ed0b47e5ff76`.

The exact APK was installed independently for both load orders on each of two
API 35 arm64-v8a emulators. The 4 KiB target reported model token
`Android-SDK-built-for-arm64`, a 4096-byte page size, and fingerprint SHA-256
`acb4e14882d5e2e5cd4b91925de599cdc88a39b95d37a63ebe48c15f3000f384`.
The 16 KiB target reported model token `sdk_gphone16k_arm64`, a 16384-byte page
size, and fingerprint SHA-256
`d4cb1bb60eaee567df547e52dfdbbd5a1898d186aa9aa64f09c9b62a962f01ee`.

All four captures reported sherpa-owned process runtime resolution, ONNX
Runtime 1.27.0, and negotiated API 27. Each completed two strict alternating
cycles, accepted and settled native Fonix cancellation, retired the bounded
sherpa detector after between-frame cancellation, suppressed the induced stale
completion, recovered both engines, passed both disposal orders and double
disposal, and settled with zero pending work. The package was force-stopped,
uninstalled, and verified absent after every run.

The retained evidence hashes are:

| Page size | Load order | Trusted capture manifest | Raw receipt | Validation record |
| --- | --- | --- | --- | --- |
| 4 KiB | `dart-first` | `d5cf010cb70782bb9d542b0d3b68e8272b676d127f3c72679ad24c21ebb40821` | `bfc81d2e02e88c0223687cde15bb0b1df91bb2feea37c912bd729092ffc29bce` | `8c9da03a622cd6e97883396d6a9a8ff9ca5ee01c0ec477aaff12f6caf319c4ff` |
| 4 KiB | `sherpa-first` | `6ab621e2f11ed7e3ce3b08b1bb60c1b228dc5f6385f6cabdf1685474fdb06abe` | `224cded3e1070074b98e3ee8b9c3b1296699e7e3606b0bdc70bc290cfbadf2ef` | `fc0f89c630b4ace1517e59db70fce6771b57a65a5040e151624666a01c4d501a` |
| 16 KiB | `dart-first` | `56f13fa49e079c60a3deec3da4ed2068a3abe7a9409fe23d099dd87fa0b14e9c` | `0ecd917d2d0fe2b1e93e570dd5c802878d4f638c7f64e61f09b5eb0345d91067` | `82b811ba6f7f51e3793a76b4816e1d41e909478c3c41e21a33c8f6088f2b855f` |
| 16 KiB | `sherpa-first` | `2c3371ce831eaae566c8abda5a6da9fe46ee857a23b919a5c69a4304ed646764` | `145f1d98440b896adfda283e5a4dde8a943ac2fda3de5b5210ba9268e943147d` | `e7255259522aec2c1fd8342ea68e1796d9a961f339127fdfc394d371cb91aa63` |

Each capture manifest records `trusted-adb-capture`. The four validation
records and the aggregate compatibility manifest remain
`offline-consistency-only`; the latter has SHA-256
`0eb7fece2610b0225060696aa8599a960d01955cc1a7bc6f2ba8d7596964f504`.
This evidence is limited to the exact API 35 emulator, arm64-v8a, Release APK,
and named 4 KiB/16 KiB tuples. It does not prove API 24 execution, a physical
device, an AAB-derived installed split, x86_64, performance, thermal behavior,
signing, distribution, or another tuple. The next target-evidence step is the
exact Linux x86_64 target-host run, while lifecycle/resource stabilization and
scoped pre-1.0 release engineering can proceed locally in parallel for only
exact advertised tuples. Android QNN and all Windows target-host, provider,
final-package, installer, and clean-machine work remain deferred.

## Required CI coverage

The checked workflow requires minimum/stable Dart format-analysis-tests,
isolated binding regeneration, both Python verifier collections, the C source
gate, release/source evidence, deterministic fixture bytes on Linux/macOS/
Windows, Linux/macOS native sanitizer jobs, the Windows shim/security
contract, exact macOS ORT native/Dart/worker/provider tests, and the fresh
macOS final-application gate. The exact macOS lane now runs both production
native-asset bindings and the explicit dynamic-adapter seam against the same
strict fake runtimes. It also runs the committed reference application from a
clean external copy after the independent bundle audit.

Android auditor/orchestration/target-runner tamper tests run in the Python CI
collection. The standalone Android Gradle package gates and CPU/XNNPACK
emulator receipts, plus the source-final sherpa static gate, all four trusted
4 KiB/16 KiB target captures, and the aggregate compatibility manifest, were
run locally with explicitly provisioned revision-pinned, byte-pinned, and
version-gated inputs described above. They are not yet hosted CI lanes or
reproducible release builds, and Gradle dependency-verification metadata
remains absent.

The Apple auditor and iOS reference-gate unit/tamper suites also run in the
ordinary Python collection. The full iOS build/audit/simulator gate is locally
provisioned evidence rather than a hosted CI lane. Its exact source epoch,
archive, toolchain, unsigned device application, audited simulator application,
transport-normalized installed tree, launch binding, receipt, settlement, and
uninstall are one
closed tuple; none may be inferred from source tests alone.

## Evidence not produced, scoped exclusions, and release blockers

The following are deliberately not claimed:

- physical iOS device execution, lifecycle, provisioning, approved device or
  distribution signing, IPA/App Store packaging, or distribution evidence;
- iOS CoreML/XNNPACK/GPU/Neural Engine assignment, representative workloads,
  sustained performance, thermal behavior, or provider qualification;
- iOS simulator Release execution or transfer of the exact Debug simulator
  result to another tuple;
- Android API 24 execution, physical-device or x86_64 inference, an installed
  AAB-derived split, or transfer of the exact 4 KiB/16 KiB emulator results to
  another tuple;
- physical-device XNNPACK assignment/parity, representative workloads,
  sustained performance, thermal behavior, and benchmark-backed provider
  qualification;
- deferred Android QNN aligned-build/device/HTP/cache/product receipts;
- Linux target-host loading/inference and final Flutter packaging;
- deferred Windows target-host loading/inference, final Flutter packaging,
  installer, and clean-machine runs;
- physical-device or representative-hardware qualification and sustained
  benchmarks for every other non-CPU provider; or
- notarization, public signing, publication, or redistribution approval.

For the planned scoped pre-1.0 release, a missing gate blocks publication only
when its exact target/provider tuple is selected for advertising. Deferred
Android QNN, every deferred Windows target-host/provider/package/install path,
and other unadvertised tuples must remain explicitly unsupported, but they are
not completion gates for that narrower release. They remain gates for
five-platform, unqualified `Release-ready`, and 1.0 claims. Existing portable,
static, source, tamper, cross-build, and loader-security regressions remain
mandatory even for excluded tuples.

In particular, the reference app's local ad-hoc build disables library
validation because it has no authorized Team ID shared with FlutterMacOS. That
explicit development entitlement is executable gate evidence, not a
distribution security/signing decision.

The repository also has no project-level `LICENSE`, approved private security
contact, signing identity, or publication authority. Those are product/legal/
release decisions and cannot be inferred from passing source tests or from the
vendored ONNX Runtime license and notices. They remain the blockers to changing
`publish_to: none` or promoting any unqualified support row.
