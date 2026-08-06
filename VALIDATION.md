# Validation Report

- Validation date: 2026-08-07
- Package: `fonix 0.1.0-dev.1`
- Host: macOS 26.5.2 (25F84), arm64

## Result

The source implementation is complete for Fonix's declared development API
and locally controllable package boundary. No known source, analysis, unit,
native-shim, exact-runtime, artifact-audit, macOS application-gate, or Android
standalone reference-gate failure remains in this snapshot. The macOS gate
applications and independently audited Android arm64-v8a Release APKs load and
execute their exact packaged CPU paths. A separate Android profile build also
passes the closed XNNPACK functional assignment/parity/fallback/recovery path
on the named API 35 arm64/4096-byte emulator.

This is not redistribution, five-platform release approval, or XNNPACK
provider qualification. The package remains `publish_to: none`, and the
external licensing, signing, target-host, physical-device, performance, and
provider-qualification blockers listed below remain authoritative.

## Implemented boundary

The checked source contains the complete app-owned wrapper layer described by
the active documentation:

- a stable 67-export C ABI over ONNX Runtime API 27 with closed status,
  runtime, session, value, composite-value, run, cancellation, profiling, and
  data-lease ownership;
- a typed Dart API for trusted runtime sources, file/byte/external-data models,
  dense tensors, strings, float16/bfloat16 storage, sequences, maps, optionals,
  copied metadata, native-backed leases, and named synchronous inference;
- bounded worker-isolate sessions and pools using wire protocol version 3,
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
  release-evidence checks; and
- a committed macOS arm64 and Android arm64-v8a Flutter reference app over the
  public Fonix library, with bounded worker ownership, exact model/runtime
  identity, cancellation, retry, lifecycle replacement, stale-result
  suppression, deterministic cleanup, and platform-specific final-package
  gates; the Android one-shot mode has separate exact CPU and XNNPACK profiles,
  with the latter owning strict assignment, exact CPU parity, explicit fallback
  report/rejection, post-rejection recovery, and deterministic five-session
  cleanup.

Sparse tensors, opaque/custom values, arbitrary provider plugins, training,
GenAI, and Web/WASM remain explicitly outside this API rather than unfinished
paths.

## Final verification summary

| Gate | Result |
| --- | --- |
| Dart formatting | 78 files, 0 changes |
| Dart analysis | No issues with `--fatal-infos` |
| Ordinary offline Dart suite | 184 passed, 16 explicitly provisioned skips |
| Worker lifecycle with strict fake and exact ORT | 26/26 passed |
| Native FFI bridge with strict fake runtimes | 11/11 passed |
| Exact ORT core Dart inference | 8/8 passed |
| Exact ORT generated Phase-3 Dart corpus | 18/18 passed |
| Exact ORT CPU/CoreML run-evidence suite | 3/3 passed |
| Python CI-script tests | 183/183 passed |
| Standalone Python verifier tests | 39/39 passed |
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
| Closed source checksum manifest | Regenerated from and verified against this final tree |

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
generator have synthetic contract/tamper coverage only. They rehash exact
inputs and enforce closed workload, lifecycle, library, and four-record matrix
invariants, but public tool hashes do not authenticate the source of target or
logcat JSON. No trusted sherpa coexistence capture has run, so these tools do
not add target-execution evidence.

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

Android auditor/orchestration tamper tests run in the Python CI collection. The
full Android Gradle package gate and emulator receipt recorded here were run
locally with explicitly provisioned revision-pinned, byte-pinned, and
version-gated inputs described above; they are not yet a hosted CI lane or a
reproducible release build, and Gradle dependency-verification metadata remains
absent.

## Evidence not produced and release blockers

The following are deliberately not claimed:

- signed final iOS application packaging, simulator execution, or physical
  iOS CPU/CoreML/XNNPACK/ANE evidence;
- Android API 24 execution, an actual 16 KiB Android runtime, physical-device or
  x86_64 inference, an installed AAB-derived split, either sherpa/Fonix load
  order, alternating speech workload, or exact sherpa product receipts;
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

In particular, the reference app's local ad-hoc build disables library
validation because it has no authorized Team ID shared with FlutterMacOS. That
explicit development entitlement is executable gate evidence, not a
distribution security/signing decision.

The repository also has no project-level `LICENSE`, approved private security
contact, signing identity, or publication authority. Those are product/legal/
release decisions and cannot be inferred from passing source tests or from the
vendored ONNX Runtime license and notices. They remain the blockers to changing
`publish_to: none` or promoting any unqualified support row.
