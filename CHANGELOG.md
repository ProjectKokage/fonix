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
- Validated exact ONNX Runtime 1.27.1 CPU inference and CoreML `CPUOnly`
  assignment/parity on macOS arm64. The Android XNNPACK result is limited to an
  API 35 arm64/4 KiB emulator functional checkpoint; API 24/16 KiB/physical-
  device and sherpa, iOS, Linux target-host, and deferred QNN/Windows gates
  remain documented qualification requirements.
