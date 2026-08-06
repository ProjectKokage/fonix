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
- [ ] Native status preserves ORT/platform errors safely.
- [ ] Each retained native reference is released exactly once; null release behavior is tested.

## Build and supply chain

- [ ] Every native artifact is pinned and SHA-256 verified.
- [ ] Archive extraction is traversal-safe.
- [ ] Lockfile/build manifest is updated.
- [ ] Licenses/notices/SBOM are updated.
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

- [ ] Exact sherpa artifact/revision and hashes are recorded.
- [ ] Shared vs static sherpa ORT was determined.
- [ ] Wrapper external mode contains no `libonnxruntime.so`.
- [ ] External shim has no `DT_NEEDED` on ORT.
- [ ] No `pickFirst`/merge-order workaround exists.
- [ ] Final APK/AAB has exactly one ORT per ABI.
- [ ] Dart-first and sherpa-first tests pass.
- [ ] Dart and sherpa workloads run alternately in one process.
- [ ] Release/R8 build passes.
- [ ] 16 KB page-size validation passes.
- [ ] QNN/custom EP uses one aligned ORT.

## CI evidence

- [ ] Dart format/analyze/unit tests pass.
- [ ] Native tests/sanitizers pass where supported.
- [ ] FFI integration tests pass.
- [ ] Artifact inspection reports are attached.
- [ ] Failure/diagnostics logs are redacted.
- [ ] Support matrix and reference snapshot are updated when needed.
