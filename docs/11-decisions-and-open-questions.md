# 11. Architecture Decisions and Open Questions

This file records decisions the coding agent should treat as accepted unless a new ADR supersedes them with evidence.

## ADR-001: project-owned C shim

**Decision:** Dart binds to a compact `dort_*` C ABI rather than directly exposing the full ONNX Runtime C API.

**Reasons:**

- stable Dart-facing ABI;
- runtime resolution for Android coexistence;
- centralized ownership/validation/status translation;
- reduced generated surface;
- provider API normalization;
- no C++ ABI exposure.

**Trade-off:** additional native code and maintenance. Accepted because the wrapper already requires platform packaging and runtime compatibility control.

## ADR-002: Dart build hooks as primary native integration

**Decision:** use Dart build hooks/code assets for the shim and pinned native artifacts, with platform-specific framework/package integration where required.

**Reasons:** current recommended Dart/Flutter FFI packaging path; one package can target Flutter and standalone Dart Native; build configuration can select target/flavor reproducibly.

**Trade-off:** requires a modern Dart SDK and careful handling of Apple/static and external native dependencies.

## ADR-003: deterministic disposal plus finalizer safety net

**Decision:** synchronous native owners expose idempotent `dispose()`.
Worker-isolate sessions and pools expose idempotent awaited `close()`. Native
finalizers cover forgotten synchronous cleanup only.

**Reasons:** ORT sessions/buffers can be large and finalizer timing/order is nondeterministic. Explicit disposal is required for predictable mobile/desktop resource behavior.

## ADR-004: copy-safe defaults, explicit host zero-copy

**Decision:** copy Dart data into wrapper-owned native memory by default. Zero-copy requires native buffers/data leases.

**Reasons:** native code must not retain pointers into movable/GC-managed Dart memory; async and output lifetimes require explicit ownership.

**Trade-off:** default copy overhead. Mitigated by reusable native buffers and I/O binding for performance-sensitive paths.

## ADR-005: synchronous core, worker-isolate async

**Decision:** the core API is synchronous. Async inference is implemented by a dedicated isolate that owns its runtime/session.

**Reasons:** FFI calls block an isolate; native handles are not safely transferable; long-lived workers amortize model/session initialization.

## ADR-006: explicit provider and fallback policy

**Decision:** callers supply an ordered provider list, per-provider assignment
requirements, and an explicit fallback policy. Fonix has no automatic
recommendation, strict preset, or benchmark preset.

**Reasons:** EP performance and coverage are model/device-specific; registration alone does not establish acceleration.

## ADR-007: one shared ORT owner on Android

**Decision:** when sherpa uses shared ORT, the final process/package has one `libonnxruntime.so` per ABI. The ordinary wrapper uses process resolution; custom EPs use an aligned build.

**Reasons:** duplicate AAR native libraries collide; arbitrary selection is not ABI/provider compatibility; two runtimes increase memory and global-state risk.

## ADR-008: prohibit Gradle `pickFirst` for ORT

**Decision:** native dependency ownership must be resolved structurally, not through archive-selection order.

**Reasons:** `pickFirst` hides the producer/version and can silently substitute an incompatible runtime.

## ADR-009: C API 27 compatibility floor in this snapshot

**Decision:** compile the base shim to the ONNX Runtime C API 27 contract and
lock the current baseline artifacts to ORT 1.27.1.

**Reasons:** current sherpa Android integration observed on 2026-08-06 uses ORT 1.27.x. API negotiation allows one base wrapper to coexist while avoiding unchecked newer function-table entries.

**Review trigger:** sherpa raises its Android floor, a security issue requires dropping 1.27.x, or a required wrapper feature cannot be implemented with API 27.

**Migration:** raise through a documented shim/package release and preserve a compatibility flavor/window where practical.

## ADR-010: no giant all-provider artifact

**Decision:** publish baseline and selected provider flavors separately.

**Reasons:** EP dependencies are large, hardware-specific, license/toolchain-coupled, and can prevent ORT from loading when absent.

## ADR-011: generic native provider options

**Decision:** the shim uses a closed API-27 registration adapter for each
supported provider and bounded UTF-8 key/value options where that adapter
accepts them; Dart adds typed constructors and repeats option validation.

**Reasons:** reduces ABI churn and follows modern ORT provider configuration direction. Typed Dart validation retains usability.

## ADR-012: no ORT unload by default

**Decision:** retain dynamic loader handles for process lifetime.

**Reasons:** ORT/providers may retain global state, worker threads, callbacks, and dependent libraries; safe unload across multiple consumers is difficult to prove.

**Trade-off:** process-lifetime native code/data residency. Sessions/values/buffers are still deterministically released.

## ADR-013: final artifact is source of packaging truth

**Decision:** verify APK/AAB/IPA/app/executable outputs, not only package manager declarations or intermediate AARs.

**Reasons:** merging, stripping, ABI splitting, signing, and loader layout occur after dependency resolution.

## ADR-014: provider activity requires evidence

**Decision:** diagnostics distinguish compiled, discoverable, registered, active, and qualified.

**Reasons:** partial graph assignment/fallback is normal and can erase performance gains.

## ADR-015: plugin and specialized EPs are trusted, separate flavors

**Decision:** plugin-based and specialized accelerator EPs are distributed in separately pinned flavors. Plugin libraries are resolved only from trusted application/package-owned locations, selected through negotiated EP-device APIs where available, and retained until all dependent sessions and objects are destroyed.

**Reasons:** MIGraphX, native WebGPU, TensorRT RTX, Vitis AI, and vendor EPs introduce hardware-specific SDK, driver, compiler, cache, and licensing constraints. An unrestricted plugin path would also turn model execution into arbitrary native-code loading.

**Trade-off:** more artifacts and qualification lanes. Accepted because a portable all-provider binary would be larger, less reproducible, and more failure-prone.

**Review trigger:** ORT establishes a portable, signed, dependency-isolated plugin distribution contract that is proven across the supported platforms.

## Open question 1: publication/package names

**Recommended default:** keep a neutral core name and encode provider/runtime flavor in package/artifact metadata rather than creating many confusing Dart APIs. Check package-name availability before publication.

## Resolution 2026-08-06: minimum OS versions

**Decision:** the exact lock tuples own each floor: macOS arm64 14.0, iOS arm64
device/simulator 15.1, Android API 24, Linux glibc 2.27, and Windows 10 for the
current baseline. A future artifact recomputes its floor from the maximum
required by Dart/Flutter, ORT, and its selected providers and records that value
in the lock and generated manifests. See the support matrix; no lower generic
package promise overrides an artifact tuple.

## Open question 3: binary hosting

Options:

- official upstream artifacts;
- project release assets with reproducible build records;
- application-owned artifact mirror;
- source builds.

**Recommended default:** official artifacts for matching baseline targets; project-built signed/checksummed assets only where needed; application-owned aligned builds for proprietary SDKs such as QNN.

## Open question 4: same package vs companion binary packages

**Recommended default:** begin with one core package and build-hook flavors. Introduce companion packages only after testing transitive native-asset composition, version resolution, and user ergonomics.

## Open question 5: macOS x86_64 and Windows arm64 release tier

**Recommended default:** support source/build paths and qualify as Tier 1 only when stable runners and current ORT artifacts/builds exist. The baseline Windows x64 target-host gate is deferred, not passed: it blocks any Windows support, five-platform, unqualified `Release-ready`, or 1.0 claim, but does not block a scoped pre-1.0 release that excludes Windows explicitly.

## Open question 6: Android `armeabi-v7a`

**Recommended default:** omit initially. Add only for a named consumer with sherpa/ORT/provider and 16 KB/package testing coverage.

## Resolution 2026-08-06: direct Dart native views

**Decision:** copied access is the safe default. Numeric tensor and native-buffer
views use a C-side data lease plus an attached Dart finalizer/retention token.
The returned tensor views are read-only, survive parent disposal, and have
exhaustive ownership/finalizer tests. No raw pointer constructor is public.

## Resolution 2026-08-06: composite ONNX values

**Decision:** sequences, maps, and optionals are implemented with recursive
closed type metadata and native ownership. Sparse tensors, opaque/custom
values, training, and GenAI remain explicitly outside the current API.

## Resolution 2026-08-06: provider assignment introspection

**Decision:** one-run assignment evidence comes from a bounded, strict ORT
Chrome-trace parser. It preserves compiled, discoverable, registered, active,
and qualified as separate facts and never infers assignment from session
creation. Unsupported evidence is reported as unavailable rather than guessed.

## Resolution 2026-08-06: sherpa runtime ownership

**Decision:** Android declares exactly one owner. Ordinary sherpa coexistence
uses a process-only Fonix shim and one sherpa-owned shared ORT; application-owned
mode bundles one exact ORT. A statically embedded sherpa ORT is unsupported for
ordinary coexistence and QNN requires an aligned shared build. Dual-runtime
mode needs a separate product requirement, ADR, and qualification.

## Open question 11: Windows ML/AutoEP

**Recommended default:** evaluate as a separate Windows integration package after the raw ORT wrapper is stable. Do not change cross-platform provider selection semantics implicitly.

## Open question 12: compatibility-floor update cadence

**Recommended default:** review on every ORT/sherpa update, but raise only for security/support/feature need. Maintain compatibility records for exact sherpa artifacts and avoid indefinite support for vulnerable runtimes.
