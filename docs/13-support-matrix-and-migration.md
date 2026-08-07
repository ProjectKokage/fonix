# 13. Support Matrix and Migration

This document separates implemented source paths from evidence-backed support.
It is deliberately conservative: compilation, archive inspection, and a
synthetic package do not establish that a final application loads and runs on
the target.

## 13.1 Status vocabulary

| Status | Meaning |
| --- | --- |
| Implemented | The closed Dart/shim/build path exists and has source-level tests. |
| Cross-built | Target bytes were produced and statically audited on another host. |
| Target-tested | The named runtime/model path executed on the target host or device. |
| Final-app tested | The packaged application bytes were audited, loaded, and exercised. |
| Provider-qualified | Assignment, parity, fallback, cache, and benchmark receipts exist for an exact provider/model/device tuple. |
| Release-ready | Every advertised target/provider gate, licensing, signing, SBOM, security, and reproducibility requirement passed. |

Fonix is not currently release-ready. No row may be promoted by inference from
a lower status.

## 13.2 Baseline platform matrix

The lock-selected baseline is ONNX Runtime 1.27.1 with C API 27 and shim ABI 1.

| Target | Locked baseline | Highest current evidence | Missing before a support claim |
| --- | --- | --- | --- |
| macOS arm64, minimum 14.0 | bundled CPU | Final-app tested with both a freshly generated gate app and the committed public-API reference `.app`, exact packaged bytes, and numeric CPU inference | Consistent distribution signing, removal/review of the local ad-hoc library-validation exception, notarization, and release approval |
| iOS arm64 device, minimum 15.1 | linked CPU | Shim/runtime source resolution and device-target compilation | Signed final app, physical-device load/inference, lifecycle, and packaging audit |
| iOS arm64 simulator, minimum 15.1 | linked CPU | Shim/runtime source resolution and simulator-target compilation | Final simulator app load/inference and packaging audit |
| Android arm64-v8a, API 24 | application-owned locked `cpu` artifact with compiled XNNPACK or sherpa-owned process shim | Final-app tested for the application-owned path: independent development-signed R8 Release APK/AAB audits and installed APK inference for the CPU and closed XNNPACK functional profiles on an API 35 arm64/4096-byte emulator. The sherpa-owned path has an exact R8 Release APK/base-only-AAB static audit plus independent trusted-`adb` `dart-first` and `sherpa-first` runs of that same APK on the same emulator; both bounded two-cycle lifecycle contracts passed | API 24 execution, actual 16 KiB runtime, physical device, installed AAB-derived splits, x86_64, physical-device/performance/thermal XNNPACK qualification, both 16 KiB sherpa load orders, four-record compatibility aggregation, approved distribution signing, and release approval; QNN is deferred |
| Android x86_64, API 24 | application-owned CPU or sherpa-owned process shim | NDK cross-build, ELF/16 KiB static audit, and synthetic final native inventory | Flutter APK/AAB, emulator runtime/inference, sherpa coexistence, and release/R8 |
| Linux x86_64, glibc 2.27 | bundled CPU | Cross-built and statically audited | Clean target-host executable/package load and real inference |
| Linux arm64, glibc 2.27 | bundled CPU | Cross-built and statically audited | Clean target-host executable/package load and real inference |
| Windows x64, Windows 10 | bundled CPU | Cross-built PE/DLL layout and Windows shim-contract CI path | Deferred until a Windows environment exists: clean target-host final application load, real inference, and installer/DLL-search evidence |

macOS x86_64, Windows arm64, Android armeabi-v7a, and every unlisted tuple
have no locked baseline artifact and are not supported by this snapshot.

## 13.3 Execution-provider matrix

| Provider | Configuration path | Highest current evidence | Qualification boundary |
| --- | --- | --- | --- |
| CPU | implicit or explicit last provider | Exact ORT 1.27.1 CPU inference and full-assignment receipts through the packaged public-API app on macOS arm64 and the application-owned Android arm64 APK on the named API 35/4 KiB emulator | Other target tuples and Android API 24/16 KiB/physical-device/AAB-split cases retain their platform gates; these tiny fixtures are not benchmark qualification |
| XNNPACK | typed options plus one canonical generic thread option and oversubscription checks | Exact ORT 1.27.1 final-app functional checkpoint on an API 35 arm64/4 KiB emulator: one-node static-weight MatMul full assignment for six runs across two sessions, exact CPU parity, fallback report/rejection, recovery, and cleanup | Not provider-qualified: the tiny fixture is not representative; physical-device assignment, API 24/16 KiB/x86_64 execution, sustained performance, thermal behavior, and benchmark receipts remain open |
| CoreML | typed format/compute/cache options | Exact macOS arm64 `CPUOnly` full assignment, CPU parity, and scoped-cache behavior | No inference about GPU/ANE; iOS and physical-device qualification remain open |
| NNAPI | typed legacy flags; Android-only native symbol | Cross-built for locked Android ABIs | Deprecated/opt-in; requires real-device assignment, parity, and fallback evidence |
| QNN | generic options plus aligned-build/receipt tooling | Contracts exercised with synthetic local artifacts only | Deferred: exact SDK/license, aligned ORT+sherpa bytes, selected device/firmware, HTP assignment, parity, and cache receipts; existing contract/tamper checks remain required |
| CUDA | provider-specific adapter | Implemented source path only | Exact flavor/dependencies, clean GPU host, assignment, parity, lifecycle, and benchmark |
| TensorRT | provider-specific adapter and ordered policy | Implemented source path only | Exact CUDA/TensorRT flavor; TensorRT to CUDA to CPU order; engine-cache and hardware receipts |
| DirectML | Windows provider-specific adapter and session constraints | Implemented source path only | Deferred until a Windows environment exists: exact flavor/device with assignment, parity, lifecycle, and benchmark |
| OpenVINO | typed normalized options | Implemented source path only | Exact CPU/GPU/NPU flavor and target receipts |
| oneDNN | provider-specific adapter | Implemented source path only | Exact provider build, target-host assignment, parity, and benchmark |
| MIGraphX | provider-specific adapter | Implemented source path only | Exact ROCm/MIGraphX flavor and AMD target receipts |

“Implemented source path” does not mean the default CPU artifact contains the
provider. Heavy provider libraries require future separate, application-local,
lock-selected flavors. Generic or specialized provider IDs not listed here are
unqualified and must not appear in a product support claim.

## 13.4 Android ownership compatibility

Every Android shim build declares exactly one owner:

- `sherpa`: external profile, process source only, no Fonix ORT artifact;
- `application`: bundled profile, exact locked Android ORT artifact, bundled
  source only.

The embedded schema-3 build information carries `androidRuntimeOwner` and the
ordered `allowedRuntimeSources` array. Runtime open checks that structure
directly. For current target evidence, a raw schema-2 receipt binds one exact
APK/ABI/build/load-order/page-size/process/workload/lifecycle run. The
repository validator rehashes its APK and all external evidence and emits a
schema-1 validation record. The schema-2 compatibility generator accepts only
those records, binds every selected source shim/ORT/sherpa library to final
loaded segments, and requires the four load-order/page-size records for every
declared ABI. The separate static package-pair generator binds one exact APK
and base-only AAB to the same selected source graph; current `sherpa-audit`
inventory remains insufficient. APK runtime evidence requires exact
audited-APK target runs; AAB runtime evidence separately requires an installed
delivered split. These offline tools establish internal contract and byte
consistency, not target/log origin; trusted runner capture is required before
promotion. Application-owned
standalone CPU and XNNPACK functional emulator receipts now exist, but neither
contains sherpa. The exact sherpa-owned Release APK now has trusted captures for
both 4 KiB load orders, but the two real 16 KiB records and four-record
aggregation remain absent, so Android coexistence remains unqualified. The
XNNPACK receipt also remains below
`Provider-qualified` because it has no physical-device or benchmark evidence.

The static compatibility tooling now has separate closed `jni` and
`flutter-ffi` profiles. The exact selected 1.13.4 arm64-v8a/x86_64 Flutter FFI
native inputs pass their C API/C++ API dependency and 16 KiB ELF checks, and
schema-2 records can bind repeatable raw `jniLibs`/Native Assets inventories to
every final loaded segment. The static package-pair path has deterministic
synthetic and tamper coverage. Source commit
`0e4effef837352685ab5ac39cd28e017973fca70` contains the bounded real
adapters, lifecycle sink, fixtures, and trusted runner; its `MANIFEST.sha256`
file has SHA-256
`e0080a0ed914fd55b5ab3e02f2593ab2c9a1c490ebf0e2129d9c2a806acfc5d6`.
The runtime-provisioned arm64-v8a R8 Release gate statically audited all eight
assets and the single-ORT graph. The resulting 45,070,602-byte APK has SHA-256
`93fd4a388a723621b404aabfc1026197d8fb11620fb2f3d2746bac594fd81e54`;
the 26,045,592-byte base-only AAB has SHA-256
`ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`.
The static package manifest/report hashes are
`a75a3f7f7004a86453cf9d6d6f686c1e5645eaf167dd44268b67d107a3b099d3`
and
`b63dd44133b8a74baa02d810e9e54905d6732a4f62e6099cf56c3bc90f28fd14`.

That exact APK and harness contract
(`963e76730336bf9c22e2641d0a28b066a2e9592d64eef2723d80b81da7217519`)
passed `dart-first` and `sherpa-first` under distinct challenges on one API 35
arm64 emulator with page size 4096 and fingerprint hash
`acb4e14882d5e2e5cd4b91925de599cdc88a39b95d37a63ebe48c15f3000f384`.
Both two-cycle trusted-`adb` captures passed cancellation, stale suppression,
recovery, both disposal orders, double disposal, and zero pending work, and the
package was absent afterward. The `dart-first` capture/receipt/validation
hashes are
`6eb2a5df4ef1b2b541522f1448cfde27ddae705d5fd810f717956a945792ad40`,
`fe72465d43b0981ca6b4d6fad18f4c63579b833369a85656c1e89a34bcb357c8`,
and `44a27782535653538616556cabbbe918e0c0efec299315e182b5b0a9fb0f1293`;
the `sherpa-first` hashes are
`2171e8eaf7685c5bcb59cf68195ddfc81fd23372926df45809f1c8c0a58815d0`,
`414b85ecf40598ec9433f9c48232d634d087b0901e34ae1e76b0c06309cd4f1b`,
and `72f52d31edce29f5bdbba8cfbdc664043d45324e90b23780fe2124588bb45dbd`.
Capture provenance is `trusted-adb-capture`; validation remains
`offline-consistency-only`. This does not promote the Android row without both
real 16 KiB load orders and required four-record aggregation, and it is not API
24, physical-device, AAB-derived-install, performance, or distribution
evidence.

The runtime receipt validator currently accepts only the sherpa-owned process
mode with the Flutter FFI C API/C++ API topology in an APK. Although the
compatibility generator retains contract-tested aligned/JNI parsing, the
current validator cannot emit target records for those modes. Synthetic
records, an AAB audit, or aligned/QNN build output cannot fill that gap.

## 13.5 Compatibility policy

- Dart package: `0.1.0-dev.1`; public API stability is not promised before
  1.0.
- Shim ABI: 1. An incompatible C boundary must increment the ABI and retain an
  explicit mismatch error.
- Required ORT API: 27. Raising it requires a new sherpa compatibility window
  and migration record.
- Native artifact lock and staged artifact manifest: schema 2.
- Embedded shim build manifest: schema 3. It is an internal protocol and can
  evolve independently of the C ABI, but stale generated native assets are
  rejected.
- Android raw load-order receipt: schema 2. Android load-order validation
  record emitted by the current validator: schema 1. Android compatibility
  manifest: schema 2. These are separate protocols; the manifest generator
  rejects a raw receipt and records that do not carry the current schema,
  verifier, and validator identities or satisfy the closed contract. Public
  tool hashes identify bytes; they do not authenticate the emitter. Current
  outputs are fixed to `claimStatus: offline-consistency-only` and unverified
  target provenance; changing that meaning requires a new closed protocol.
- Runtime-information and public-diagnostics envelopes: their independent
  closed schema 1 payloads. Provider discovery/evidence also use their own
  schema 1 payloads, while the worker-isolate wire protocol is version 3. Its
  startup contract resumes only after parent ownership and timeout setup,
  publishes an authoritative command port before fallible native setup, and
  correlates readiness to that same port.
- Artifact/runtime: exact lock identity. Substituting a same-named library is
  not an upgrade path.

## 13.6 Migrating an earlier preview checkout

1. Delete only generated build outputs and rebuild the Fonix native asset;
   never reuse an embedded build manifest older than schema 3. Do not confuse
   it with the current schema-2 native lock/staged artifact manifest.
2. Keep the committed dependency graph and exact artifact cache; do not run a
   broad dependency or ONNX Runtime upgrade as migration setup.
3. Select the runtime source permitted by the rebuilt schema-3 manifest. iOS
   arm64 defaults to linked; non-iOS desktop external builds accept process or
   trusted file sources; bundled builds require `runtime_mode: bundled` plus an
   explicit cache/mirror. On Android, `android_runtime_owner` derives the only
   permitted mode, so remove any contradictory override.
4. Replace raw CoreML `ModelCacheDirectory` options with
   `OrtCoreMlCacheConfiguration`. Supply the caller-known lowercase model
   SHA-256 and an existing private absolute artifact root.
5. Supply `artifactRoot` whenever non-CPU provider reporting, assignment proof,
   explicit profiling, optimized-model output, or CoreML caching requires
   native output artifacts.
6. Treat `registered` and `discoverable` provider diagnostics as creation facts.
   Read `OrtRunResult.providerEvidence` for active/full-assignment claims.
7. Replace preview `OrtProviderPolicy` values with the ordered
   `OrtSessionOptions.providers` list, each `OrtProviderRequirement`, and one
   `OrtFallbackPolicy`.
8. Remove obsolete generic type arguments from `OrtTensor` and
   `OrtNativeBuffer`. Replace preview `OrtNativeBuffer.float32` with
   `OrtNativeBuffer.allocate`, `withTypedData` with bounded `write` or
   `viewBytes`, and `withFloat32View` with retained `viewFloat32Data`. Tensor
   copy constructors now require named `runtime`, data, and shape arguments.
9. Recreate worker-isolate sessions instead of transferring native handles.
   Use idempotent `dispose()` for synchronous native owners and await
   idempotent `close()` for isolate sessions and pools.
10. Re-run the exact final-package and target/device gates for every tuple the
   application intends to advertise.

## 13.7 Release blockers in this snapshot

- The repository has no project-level `LICENSE`; the ONNX Runtime license and
  third-party notices do not authorize Fonix distribution by themselves.
- No approved private security-reporting contact, signing identity, or
  publication authority is recorded.
- iOS, Android, Linux, and Windows still have target/final-application gaps
  listed above.
- No non-CPU provider has the complete advertised hardware qualification
  matrix. The macOS CoreML receipt is intentionally limited to `CPUOnly`.
- The Android sherpa runtime-provisioned composition and exact Release APK/AAB
  passed static audit, and both 4 KiB load orders passed trusted target capture.
  Product compatibility still requires both real 16 KiB load orders and the
  complete four-record aggregation for each claimed ABI/build.
- Android QNN product receipts require an exact SDK/license, aligned build,
  selected device/firmware, and redistribution decision that are not present.
- Android QNN qualification and Windows target-host/package work are explicitly
  deferred. Their existing source and cross-build evidence does not promote a
  support row.

Release-evidence generation must continue to report these as fail-closed
readiness conditions; tools and documentation must not synthesize approvals.
