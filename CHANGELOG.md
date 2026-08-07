## 0.1.0-dev.1

- Added the project-owned C shim, API-27 negotiation, typed errors, runtime
  identity enforcement, and deterministic disposal.
- Added dense and string tensors, reduced-precision storage, composite ONNX
  values, metadata, native buffers, leases, external-data policy, named runs,
  and cancellation.
- Added worker-isolate sessions, bounded session pools, and an exact public
  cancellation disposition that distinguishes queued removal from an accepted
  active native termination request.
- Added execution-provider discovery, normalized diagnostics, strict per-run
  assignment evidence, typed mobile-provider configuration, and CoreML cache
  identity isolation.
- Added five-platform build-hook paths, pinned artifact resolution, Android
  single-runtime ownership enforcement, final-package auditors, aligned-build
  tooling, SBOM generation, and source checksum verification.
- Added a committed macOS arm64 and Android arm64-v8a Flutter reference
  application that uses only the public API and owns worker
  lifecycle/cancellation/retry.
- Added the Android application-owned CPU gate: exact R8 Release APK/AAB audit,
  development-signature and single-ORT enforcement, and an API 35 arm64/4 KiB
  emulator CPU/full-assignment receipt from the audited APK.
- Added the closed Android XNNPACK functional gate with canonical thread-option
  validation, a deterministic static-weight MatMul fixture, strict one-node
  assignment, exact CPU parity, explicit fallback report/rejection, recovery,
  repeated-session lifecycle checks, failure-path profile-root retirement, and
  exact final APK/AAB model audits.
- Added a locked sherpa-owned Android reference scaffold and staged build gate
  that binds all hosted package trees, the generated plugin graph, exact raw
  native inputs, and one arm64-v8a Release APK/base-only-AAB static pair.
- Added the real bounded sherpa-owned Android qualification path: deterministic
  Fonix/VAD fixtures, process-runtime Fonix and sherpa adapters, authoritative
  cancellation/stale-result publication, recovery and disposal checks, and a
  trusted UID/PID-bound one-tuple target runner.
- Added atomic opt-in publication of a successful Android sherpa static-gate
  report to a new private external evidence path, while preserving stdout JSON
  compatibility and failing closed on path, identity, or publication races.
- Kept generated `.bin` qualification fixtures uncompressed and made the
  static package gate enforce the target runner's closed archive expansion
  ratio before an APK can reach installation.
- Bound the trusted runner to ADB's exact successful push-install stderr
  receipt while continuing to reject arbitrary warnings and cleanup the
  dedicated package after any failure.
- Passed the runtime-provisioned static gate after implementation commit
  `635e97d` and staged-build stabilization commit `eaa195a` on macOS arm64
  with Flutter revision
  `bd1e75d918605c91b411e8789fb911e6c9a84534` and OpenJDK 21.0.12. The exact
  R8 Release APK/AAB pair and all eight qualification assets were audited.
- Rebuilt the sherpa-owned Release package from source-final implementation
  commit `8a9b6812c17237aeab7ec6d933f23932668c4b33` and source manifest
  `3728b31a38c246328cbeda7af14a7ba502489f6e89046371d2c22d29f264efd2`,
  then passed the complete trusted four-record matrix: `dart-first` and
  `sherpa-first` on API 35 arm64-v8a/4 KiB and 16 KiB emulators. Every run
  passed the two-cycle workload and full cancellation, stale-result,
  recovery, disposal, and zero-pending-work contracts; the package was
  uninstalled with verified absence after each run. The aggregate
  compatibility manifest is retained as `offline-consistency-only`, so this
  remains exact-tuple evidence rather than a release or support-promotion
  claim.
- Validated exact ONNX Runtime 1.27.1 CPU inference and CoreML `CPUOnly`
  assignment/parity on macOS arm64. The Android XNNPACK result is limited to an
  API 35 arm64/4 KiB emulator functional checkpoint. Android API 24,
  physical-device, AAB-split-install, x86_64, performance, signing, and
  distribution evidence remain open. The next target-host gates are iOS arm64
  and Linux x86_64; Android QNN and Windows remain deferred.
