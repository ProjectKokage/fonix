# Change Acceptance Checklist

## API and ownership

- [ ] Public API change is documented and versioned.
- [ ] Every synchronous native owner has idempotent `dispose()`; every isolate
      session/pool has idempotent awaited `close()`.
- [ ] Finalizer behavior is a safety net and has stress coverage.
- [ ] No native pointer into Dart-managed memory is retained.
- [ ] Shape/byte calculations use checked arithmetic.
- [ ] Use-after-dispose fails before FFI.
- [ ] Parent/child/data-lease release orders are tested.

## C ABI

- [ ] `src/dort.h` is the only Dart-facing native contract.
- [ ] Generated bindings were regenerated and committed.
- [ ] Shim ABI mismatch test passes.
- [ ] Unsupported ORT API test passes.
- [ ] Exported symbols match the allowlist.
- [ ] C/C++ ABI leakage is absent.
- [ ] The structural native C ABI/export baseline reproduces exactly; any
      drift was classified and intentionally reviewed before regeneration.
- [ ] Native status preserves ORT/platform errors safely.
- [ ] Each retained native reference is released exactly once; null release behavior is tested.

## Build and supply chain

- [ ] Every native artifact is pinned and SHA-256 verified.
- [ ] Archive extraction is traversal-safe.
- [ ] Tracked release source is exported from one clean revision with
      `git archive`.
- [ ] Lockfile/build manifest is updated.
- [ ] Licenses and notices are updated.
- [ ] No moving `latest` URL/version resolution is used.
- [ ] Clean/offline or mirrored build behavior is tested as relevant.

## Platforms

- [ ] Relevant final application package was built and run.
- [ ] Architecture/deployment target/imports/exports were inspected.
- [ ] No global ORT installation masked packaging.
- [ ] Debug symbols/build IDs can identify the native artifact.

## Execution providers

- [ ] Provider dependencies are in a separate/declared flavor.
- [ ] Wrapper ID, registration name/mechanism, and reported provider name are not conflated.
- [ ] Registration success is tested.
- [ ] Assignment/fallback evidence is captured.
- [ ] Numerical parity/tolerance passes.
- [ ] Cold/warm performance is measured for changed defaults/claims.
- [ ] Cache identity/invalidation is tested where applicable.
- [ ] Session/threading constraints are enforced.
- [ ] Plugin EP registration/device selection/unregistration lifetime is tested where applicable.

## Android and sherpa-onnx

- [ ] Exact sherpa publication/source revision, native-input identities, and
      hashes are recorded.
- [ ] One closed `jni` or `flutter-ffi` native-library profile is selected for
      every delivered ABI; missing, duplicate, and mixed consumers are rejected.
- [ ] Shared vs static sherpa ORT was determined.
- [ ] Wrapper external mode contains no `libonnxruntime.so`.
- [ ] External shim has no `DT_NEEDED` on ORT.
- [ ] No `pickFirst`/merge-order workaround exists.
- [ ] Final APK/AAB has exactly one ORT per ABI.
- [ ] Raw schema-2 Dart-first and sherpa-first receipts are independently
      validated into schema-1 records that bind the exact final APK, harness,
      fixtures, UID/PID claims, runtime, schema, verifier, and validator.
- [ ] Trusted runner provenance authenticates installation, raw `adb`/package-
      manager/logcat capture, and the exact installed APK; offline JSON/hash
      consistency alone is not reported as target execution.
- [ ] Every claimed ABI/build has four distinct records: both load orders on
      4 KiB and 16 KiB environments; the closed matching-AAB static
      coexistence gate is implemented and passes, and remains static-only until
      a delivered split is installed and exercised.
- [ ] Dart and sherpa workloads run alternately in one process.
- [ ] Release/R8 build passes.
- [ ] 16 KB page-size validation passes.
- [ ] If QNN/custom EP support is claimed, it uses one aligned ORT; target
      qualification remains deferred until the required inputs exist.

## CI evidence

- [ ] Dart format/analyze/unit tests pass.
- [ ] Native tests/sanitizers pass where supported.
- [ ] FFI integration tests pass.
- [ ] Artifact inspection reports are attached.
- [ ] Failure/diagnostics logs are redacted.
- [ ] Support matrix and reference snapshot are updated when needed.

## Release

- [ ] Every advertised composition has passed its target-execution,
      provider-assignment, final-package, audit, and signing checks.
- [ ] Downloaded, provisioned, and final release artifacts have verified size
      and SHA-256 identities.
- [ ] The source archive, licenses, notices, support matrix, migration notes,
      and known limitations are included.
- [ ] The chosen distribution channel's install and signing flow was tested.
- [ ] Unsupported and deferred rows remain explicit; narrow evidence is not
      described as five-platform, unqualified `Release-ready`, or 1.0 support.
