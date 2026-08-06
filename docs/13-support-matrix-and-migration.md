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
| macOS arm64, minimum 14.0 | bundled CPU | Final-app tested with a Release Flutter `.app`, exact packaged bytes, and numeric CPU inference | Distribution signing/notarization and release approval |
| iOS arm64 device, minimum 15.1 | linked CPU | Shim/runtime source resolution and device-target compilation | Signed final app, physical-device load/inference, lifecycle, and packaging audit |
| iOS arm64 simulator, minimum 15.1 | linked CPU | Shim/runtime source resolution and simulator-target compilation | Final simulator app load/inference and packaging audit |
| Android arm64-v8a, API 24 | application-owned CPU or sherpa-owned process shim | NDK cross-build, ELF/16 KiB static audit, and synthetic final native inventory | Flutter APK/AAB, exact sherpa artifact, both load orders, inference, release/R8, and 4/16 KiB devices |
| Android x86_64, API 24 | application-owned CPU or sherpa-owned process shim | NDK cross-build, ELF/16 KiB static audit, and synthetic final native inventory | Flutter APK/AAB, emulator runtime/inference, sherpa coexistence, and release/R8 |
| Linux x86_64, glibc 2.27 | bundled CPU | Cross-built and statically audited | Clean target-host executable/package load and real inference |
| Linux arm64, glibc 2.27 | bundled CPU | Cross-built and statically audited | Clean target-host executable/package load and real inference |
| Windows x64, Windows 10 | bundled CPU | Cross-built PE/DLL layout and Windows shim-contract CI path | Clean target-host final application load, real inference, and installer/DLL-search evidence |

macOS x86_64, Windows arm64, Android armeabi-v7a, and every unlisted tuple
have no locked baseline artifact and are not supported by this snapshot.

## 13.3 Execution-provider matrix

| Provider | Configuration path | Highest current evidence | Qualification boundary |
| --- | --- | --- | --- |
| CPU | implicit or explicit last provider | Exact ORT 1.27.1 CPU inference and full-assignment receipt on macOS arm64; final-app CPU run | Other target tuples retain their platform gates |
| XNNPACK | typed options and oversubscription checks | Implemented and failure-tested | Real mobile device assignment, parity, thermal behavior, and benchmark |
| CoreML | typed format/compute/cache options | Exact macOS arm64 `CPUOnly` full assignment, CPU parity, and scoped-cache behavior | No inference about GPU/ANE; iOS and physical-device qualification remain open |
| NNAPI | typed legacy flags; Android-only native symbol | Cross-built for locked Android ABIs | Deprecated/opt-in; requires real-device assignment, parity, and fallback evidence |
| QNN | generic options plus aligned-build/receipt tooling | Contracts exercised with synthetic local artifacts only | Exact SDK/license, aligned ORT+sherpa bytes, selected device/firmware, HTP assignment, parity, and cache receipts |
| CUDA | provider-specific adapter | Implemented source path only | Exact flavor/dependencies, clean GPU host, assignment, parity, lifecycle, and benchmark |
| TensorRT | provider-specific adapter and ordered policy | Implemented source path only | Exact CUDA/TensorRT flavor; TensorRT to CUDA to CPU order; engine-cache and hardware receipts |
| DirectML | Windows provider-specific adapter and session constraints | Implemented source path only | Exact Windows flavor/device with assignment, parity, lifecycle, and benchmark |
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
directly. A
final compatibility record additionally binds every ABI's shim, ORT, sherpa,
loaded segments, final APK/AAB, build type, page-size environment, and both
load-order receipts. No exact product sherpa artifact or device receipt exists
in this repository snapshot, so Android coexistence remains unqualified.

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
- Android sherpa/QNN product receipts require exact external artifacts, licenses,
  and devices that are not present in this workspace.

Release-evidence generation must continue to report these as fail-closed
readiness conditions; tools and documentation must not synthesize approvals.
