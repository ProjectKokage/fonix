## 0.1.0-dev.1

- Added the project-owned C shim, API-27 negotiation, typed errors, runtime
  identity enforcement, and deterministic disposal.
- Added dense and string tensors, reduced-precision storage, composite ONNX
  values, metadata, native buffers, leases, external-data policy, named runs,
  and cancellation.
- Added worker-isolate sessions, bounded session pools, and an exact public
  cancellation disposition that distinguishes queued removal from an accepted
  active native termination request.
- Hardened worker failure ordering with acknowledged startup retirement and one
  ordered error/exit lifecycle channel; hardened cancellation with unlocked
  per-token native settlement, retryable failures, and non-reused monotonic
  tokens.
- Added bounded, replayable composed worker/pool lifecycle traces and made
  positive-int64 worker request IDs permanently exhaust instead of wrapping;
  exhausted workers no longer hide retriable capacity on another live pool
  worker.
- Enforced the exact strict-JSON nesting bound for empty containers and added a
  deterministic generated/mutated protocol corpus.
- Released bounded ORT-owned provider and model-metadata outputs even when ORT
  also reports an error, while preventing malformed oversized counts from
  authorizing pointer-array cleanup iteration.
- Expanded deterministic native fail-Nth coverage from 29 to 76 portable
  operation points, including canonical paths, run options, complete profiling,
  external-data sessions, sequence values, synchronous runs/results,
  cancellation registration, data leases, and tensor diagnostics. A separate
  provisioned matrix covers 50 exact-ORT dense, string, ZipMap, optional, and
  string-accessor fail points. Status-field allocation failure now preserves
  the original domain and numeric codes with static diagnostic fallbacks.
  POSIX profiling retains the exact root/private child identity, publishes only
  after checked cleanup, and keeps incomplete retirement retryable; POSIX
  profiling roots must be effective-user-owned and private from group/world
  writers.
- Added execution-provider discovery, normalized diagnostics, strict per-run
  assignment evidence, typed mobile-provider configuration, and CoreML cache
  identity isolation.
- Added a macOS CPU-benchmark host observer that reads the public
  `NSProcessInfo` thermal enum and low-power boolean, cross-checks low-power
  state against bounded `pmset` active-source and configured-profile output,
  and publishes the profile only as a domain-separated digest. A stable,
  complete observation is only matchable input for later baseline review;
  Apple `nominal` can also mean thermals were undetermined, a stable elevated
  state still requires review, and Linux thermal observation remains
  unavailable.
- Added five-platform build-hook paths, pinned artifact resolution, Android
  single-runtime ownership enforcement, final-package auditors, aligned-build
  tooling, SBOM generation, and source checksum verification.
- Added a committed iOS arm64, macOS arm64, and Android arm64-v8a Flutter
  reference application that uses only the public API and owns worker
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
- Added the arm64-only iOS 15.1 reference scaffold with linked-runtime
  selection, no committed development team or provisioning identity, bounded
  resident smoke publication, challenge/PID correlation, and deterministic
  process settlement. An app-owned, argument-free launch channel converts the
  native process environment into only a cached null-or-closed activation;
  Dart revalidates it with a bounded wait and never receives the raw
  environment.
- Added the manifest-bound iOS reference gate. The current frozen source epoch
  passes 72/72 app tests and analysis, including 25/25 reference-smoke tests
  and 5/5 iOS project-contract tests, and the simulator application passes two
  consecutive linked Debug builds. The gate binds installation through every
  directory, path, byte, and the retained `Runner` executable bit while
  permitting only `simctl`'s exact three-framework executable-bit
  normalization. The complete unsigned-device Release `.app` build/static
  audit and simulator install/run result is `PASS (2026-08-07)`.
- Extended the Apple final-application auditor with exact iOS arm64 Mach-O,
  deployment-floor, dependency, RPATH, export, hook-provenance, transformation,
  and signing profiles. Its device result is static-only, while simulator
  execution is exact-Debug-tuple evidence. The final bundle excludes a
  separately packaged ORT Mach-O and audited ORT load-command dependency but
  cannot prove the absence of runtime `dlopen`, another static ORT copy, or
  exactly-one static archive linkage.
- Validated exact ONNX Runtime 1.27.1 CPU inference and CoreML `CPUOnly`
  assignment/parity on macOS arm64. The Android XNNPACK result is limited to an
  API 35 arm64/4 KiB emulator functional checkpoint. Android API 24,
  physical-device, AAB-split-install, x86_64, performance, signing, and
  distribution evidence remain open. Linux x86_64 is the next target-host
  gate, followed by scoped pre-1.0 stabilization for exact advertised tuples.
  Android QNN and all Windows target-host, provider, final-package, installer,
  and clean-machine work remain deferred; no five-platform or 1.0 claim is
  made.
