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
- Rebuilt the source-final sherpa-owned Release package from implementation
  commit `0e4effef837352685ab5ac39cd28e017973fca70`, then passed trusted
  `dart-first` and `sherpa-first` captures with the exact
  `93fd4a388a723621b404aabfc1026197d8fb11620fb2f3d2746bac594fd81e54`
  APK on an API 35 arm64-v8a/4 KiB emulator. Both two-cycle workload and full
  cancellation/stale/recovery/disposal contracts passed, and the package was
  uninstalled with verified absence after each run. Validator records remain
  `offline-consistency-only`.
- Validated exact ONNX Runtime 1.27.1 CPU inference and CoreML `CPUOnly`
  assignment/parity on macOS arm64. The Android XNNPACK result is limited to an
  API 35 arm64/4 KiB emulator functional checkpoint; API 24/16 KiB/physical-
  device and complete sherpa four-record matrix, iOS, Linux target-host, and
  deferred QNN/Windows gates remain documented qualification requirements.
