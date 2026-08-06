# Validation Report

- Validation date: 2026-08-06
- Package: `fonix 0.1.0-dev.1`
- Host: macOS 26.5.2 (25F84), arm64

## Result

The source implementation is complete for Fonix's declared development API
and locally controllable package boundary. No known source, analysis, unit,
native-shim, exact-runtime, artifact-audit, or macOS application-gate failure
remains in this snapshot. Both the freshly generated gate application and the
committed public-API reference application load and execute their exact
packaged CPU paths.

This is not a redistribution or five-platform release approval. The package
remains `publish_to: none`, and the external licensing, signing, target-host,
device, and provider-qualification blockers listed below remain authoritative.

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
- a committed macOS arm64 Flutter reference app over the public Fonix library,
  with bounded worker ownership, exact model/runtime identity, cancellation,
  retry, lifecycle replacement, stale-result suppression, and deterministic
  cleanup.

Sparse tensors, opaque/custom values, arbitrary provider plugins, training,
GenAI, and Web/WASM remain explicitly outside this API rather than unfinished
paths.

## Final verification summary

| Gate | Result |
| --- | --- |
| Dart formatting | 71 files, 0 changes |
| Dart analysis | No issues with `--fatal-infos` |
| Ordinary offline Dart suite | 182 passed, 15 explicitly provisioned skips |
| Worker lifecycle with strict fake and exact ORT | 26/26 passed |
| Native FFI bridge with strict fake runtimes | 11/11 passed |
| Exact ORT core Dart inference | 8/8 passed |
| Exact ORT generated Phase-3 Dart corpus | 18/18 passed |
| Exact ORT CPU/CoreML run-evidence suite | 3/3 passed |
| Python CI-script tests | 128/128 passed |
| Standalone Python verifier tests | 33/33 passed |
| FFI binding regeneration | Both generated bindings reproduced exactly |
| Deterministic fixture inventory | 23 files and 23 closed model/data paths |
| Exact ORT standalone fixture CTests | 2/2 passed |
| Native POSIX CTests with exact ORT | 14/14 passed |
| Native POSIX ASan+UBSan CTests with exact ORT | 14/14 passed; Apple LeakSanitizer unavailable |
| Adjacent bundled-runtime CTests | 3/3 passed |
| C source-quality gate | 33 files, 713,667 bytes |
| Five-artifact offline audit | 8/8 passed |
| macOS/iOS build-hook suite with exact archives | 28/28 passed |
| Android API-24 build/audit suite | 4/4 passed |
| Linux x64/arm64 and Windows x64 Zig cross-build | 1/1 passed |
| Flutter asset publication | 7/7 passed |
| Fresh final macOS Flutter application gate | Passed, including packaged CPU inference |
| Committed macOS public-API reference application gate | 26 app tests, Release build, exact full-CPU receipt, double close, and independent final-app audit passed |
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
bundled loader passed adjacency, Flutter-framework, and unexpected-layout
tests.

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

## macOS application and provider evidence

The final application gate used Xcode 26.6 (17F113) and Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` (Flutter 3.47.0-0.1.pre, Dart
3.13.0-282.1.beta). It created a fresh Release application at a macOS 14.0
floor, audited the packaged manifest/notices and arm64 Mach-O identities,
verified code signatures and loaded-section parity with the lock-selected
runtime, then loaded only through the packaged shim/framework layout and
produced the expected CPU outputs.

The separate committed-reference gate copied `example/` outside the package
checkout, reproduced the exact manifest/notices, passed 26 controller/widget
tests and analysis, built Release, and verified signatures, hardened runtime,
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

## Evidence not produced and release blockers

The following are deliberately not claimed:

- signed final iOS application packaging, simulator execution, or physical
  iOS CPU/CoreML/XNNPACK/ANE evidence;
- a Flutter-produced Android APK/AAB, both sherpa/Fonix load-order runs, 4 KiB
  and 16 KiB device execution, physical-device inference, or aligned exact
  sherpa/QNN product receipts;
- Linux or Windows target-host loading/inference, final Flutter packages,
  installers, or clean-machine runs;
- physical-device or representative-hardware qualification and sustained
  benchmarks for non-CPU providers; or
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
