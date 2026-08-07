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
| Android arm64-v8a, API 24 | application-owned locked `cpu` artifact with compiled XNNPACK or sherpa-owned process shim | Final-app tested for the application-owned path: independent development-signed R8 Release APK/AAB audits and installed APK inference for the CPU and closed XNNPACK functional profiles on an API 35 arm64/4096-byte emulator. The sherpa-owned path has an exact commit-bound static audit and a passing four-record schema-2 matrix for both load orders on API 35 arm64 4 KiB/16 KiB emulators against one release-minified APK | API 24 execution, physical device, installed AAB-derived splits, x86_64, physical-device/performance/thermal XNNPACK qualification, approved distribution signing, and release approval; QNN is deferred |
| Android x86_64, API 24 | application-owned CPU or sherpa-owned process shim | NDK cross-build, ELF/16 KiB static audit, and synthetic final native inventory | Flutter APK/AAB, emulator runtime/inference, sherpa coexistence, and release/R8 |
| Linux x86_64, glibc 2.27 | bundled CPU | Cross-built and statically audited | Clean target-host executable/package load and real inference |
| Linux arm64, glibc 2.27 | bundled CPU | Cross-built and statically audited | Clean target-host executable/package load and real inference |
| Windows x64, Windows 10 | bundled CPU | Cross-built PE/DLL layout and Windows shim-contract CI path | Deferred until a Windows environment exists: clean target-host final application load, real inference, and installer/DLL-search evidence |

macOS x86_64, Windows arm64, Android armeabi-v7a, and every unlisted tuple
have no locked baseline artifact and are not supported by this snapshot.

## 13.3 Execution-provider matrix

| Provider | Configuration path | Highest current evidence | Qualification boundary |
| --- | --- | --- | --- |
| CPU | implicit or explicit last provider | Exact ORT 1.27.1 CPU inference and full-assignment receipts through the packaged public-API app on macOS arm64 and the application-owned Android arm64 APK on the named API 35/4 KiB emulator | Other target tuples and Android API 24/physical-device/AAB-split cases retain their platform gates; the application-owned path still lacks a 16 KiB run, and these tiny fixtures are not benchmark qualification |
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
promotion. Application-owned standalone CPU and XNNPACK functional emulator
receipts now exist, but neither contains sherpa. The corrected exact
sherpa-owned release-minified APK has trusted captures for both load orders on
4 KiB and 16 KiB API 35 emulators, and all four validation records pass
schema-2 aggregation. This exact matrix does not promote the API 24 row or
another device/ABI/build. The XNNPACK receipt also remains below
`Provider-qualified` because it has no physical-device or benchmark evidence.

The static compatibility tooling now has separate closed `jni` and
`flutter-ffi` profiles. The exact selected 1.13.4 arm64-v8a/x86_64 Flutter FFI
native inputs pass their C API/C++ API dependency and 16 KiB ELF checks, and
schema-2 records can bind repeatable raw `jniLibs`/Native Assets inventories to
every final loaded segment. The static package-pair path has deterministic
synthetic and tamper coverage. The corrected build is bound to commit
`8a9b6812c17237aeab7ec6d933f23932668c4b33` and source-manifest SHA-256
`3728b31a38c246328cbeda7af14a7ba502489f6e89046371d2c22d29f264efd2`.
The runtime-provisioned arm64-v8a release-minified gate statically audited all
eight assets and the single-ORT graph. The resulting 45,070,602-byte APK has
SHA-256
`35cca3502504b07d2d21fd27cb46fd5833fab9c6337115adfd5a309818a28ac3`;
the 26,045,592-byte base-only AAB has SHA-256
`ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`.
The static package manifest/report hashes are
`99ccfb2f88eb16b9a7fdd3e03edf39c1529f771dedf210f5e481d85b3afd60e9`
and
`6ad082a3cdf3a498f88504e24a47eb6e239a0d62f13ea02e11b7fbe202e4764e`.

That exact APK and harness contract
(`5460d723423515d519819e6e369646e6f6cd70edb89760c02da133da40cb7f23`)
passed both load orders under distinct challenges on API 35 arm64 emulators
with page sizes 4096 and 16384. All four two-cycle trusted-`adb` captures passed
cancellation, stale suppression, recovery, both disposal orders, double
disposal, and zero pending work. Their capture/receipt/validation hashes are:

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

The four validation records pass the schema-2 compatibility aggregate with
SHA-256
`0eb7fece2610b0225060696aa8599a960d01955cc1a7bc6f2ba8d7596964f504`.
Capture provenance remains separate `trusted-adb-capture` evidence; validators
and aggregate remain `offline-consistency-only`. The result is not API 24,
physical-device, AAB-derived-install, another ABI/build, performance, signing,
distribution, or general Android support evidence.

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
- The corrected Android sherpa APK/AAB passed the static audit, and the exact
  APK passed the API 35 arm64-v8a release-minified four-record 4 KiB/16 KiB
  matrix. API 24, physical-device, delivered AAB split, other ABI/build,
  performance, signing, distribution, and general Android support remain open.
- Android QNN product receipts require an exact SDK/license, aligned build,
  selected device/firmware, and redistribution decision that are not present.
- Android QNN qualification and Windows target-host/package work are explicitly
  deferred. Their existing source and cross-build evidence does not promote a
  support row.

The active non-deferred sequence is the iOS arm64 reference application, then
the Linux x86_64 target-host package, then cross-platform stabilization and
release engineering for the qualified targets.

Release-evidence generation must continue to report these as fail-closed
readiness conditions; tools and documentation must not synthesize approvals.
