# 9. Security, Release, and Maintenance

## 9.1 Threat model

The wrapper processes complex binary model files and loads native code with broad process privileges. Threats include:

- malformed or adversarial ONNX/model external-data inputs;
- integer overflow and out-of-bounds tensor sizes;
- unsafe native lifetime handling;
- dynamic-library search hijacking;
- malicious or substituted build artifacts;
- archive path traversal during build-hook extraction;
- incompatible provider DLL/shared-library substitution;
- stale vulnerable ORT/provider dependencies;
- cache poisoning or cross-model cache reuse;
- accidental disclosure through logs/profiling;
- arbitrary custom-op/plugin loading;
- denial of service through huge models, allocations, thread counts, or long-running inference.

The package cannot make arbitrary native model execution a security sandbox. Applications handling untrusted models should isolate inference in a separate constrained process where the platform permits it.

## 9.2 Model input policy

- Treat model bytes and external data as untrusted binary input.
- Offer application-configurable maximum model size, external-data size, tensor allocation, rank, dimension, and run-time limits where enforceable.
- Validate file paths and external-data base directories.
- Reject traversal outside the allowed model root.
- Avoid following symlinks outside an allowed root when the application requests sandboxed loading.
- Prefer read-only file handles/paths and avoid writable model directories for shared caches.
- Expose model SHA-256/content identity to applications and diagnostics.
- Do not deserialize a model merely to obtain metadata outside ORT unless the parser is separately maintained and hardened.

ORT security fixes are a reason to keep the runtime update process active; do not freeze indefinitely to match an old embedded dependency.

## 9.3 Resource controls

The wrapper should expose or enforce:

- checked tensor byte lengths;
- maximum native buffer allocation;
- bounded diagnostics/ring buffers;
- explicit thread counts;
- bounded worker/session pool size;
- cancellation/termination support where ORT permits;
- cache directory quotas/invalidation hooks at application level;
- model-load/run timeouts at the application/process layer when ORT cannot guarantee them.

A cancellation request is not a hard security timeout unless the underlying runtime/provider guarantees timely termination. For hostile workloads, use process isolation and OS resource limits.

## 9.4 Dynamic library security

### General

- Bundled runtime paths are derived from verified build assets, not user input.
- Explicit desktop paths must be absolute and optionally constrained to an allowed installation root.
- Reject relative paths by default.
- Keep provider libraries adjacent to the matching ORT artifact.
- Do not search the current working directory.
- Do not mutate global `PATH`/`LD_LIBRARY_PATH` in normal operation.
- Do not load arbitrary EP/custom-op libraries through the stable API.
- Record canonical library identity and digest when possible.

### Windows

Use secure `LoadLibraryExW` behavior and an application-local dependency directory. Avoid legacy search-order behavior. Provider DLL load failures must identify missing dependencies without exposing sensitive full paths in default telemetry.

### POSIX/Android

Use `RTLD_NOW` and constrained names/paths. The Android process mode resolves only the intended `libonnxruntime.so` in the application namespace. Do not search writable shared storage.

### Apple

iOS uses linked/framework packaging. Do not attempt arbitrary runtime code
loading. Validate framework signing and embedding. The committed arm64
reference gate's unsigned-device policy is a closed development exception: the
root app/executable have no signature or provisioning profile, and exactly
three nested frameworks carry teamless ad-hoc signatures. Its simulator branch
uses the exact strict development-signature inventory. Neither policy is
approved device or distribution signing, provisioning, IPA, App Store, or
release evidence.

For linked iOS, inspect every final Mach-O and load command and bind the
packaged shim to the validated native-assets hook output. The current auditor's
`linkedRuntimeIdentity` records the packaged and reference shim identities,
hook invocation metadata, normalized runtime fields and hash, comparison scope,
accounted transformations, embedded schema-3 build identity, nlist/dyld
exports, and closed ORT Mach-O/dependency findings. This proves that no
separately packaged raw Mach-O or audited load-command dependency is
attributable to ORT. It does not prove the absence of runtime `dlopen`, another
static ORT copy inside a different Mach-O, or exactly-one static archive
linkage; retain build-time/link-map evidence for those stronger claims.

## 9.5 Build-hook supply-chain controls

- Pin every URL/source revision and SHA-256.
- Use HTTPS and digest verification; TLS alone is insufficient.
- Extract archives with traversal and symlink checks.
- Restrict expected filenames and total extracted size.
- Never execute downloaded helper scripts before verification.
- Prefer source builds from official release tags for custom artifacts.
- Record patches and their hashes.
- Support offline/mirrored builds without weakening verification.
- Do not print credentials or signed URLs.
- Keep caches content-addressed and revalidate before use.
- Run the chosen distribution channel's artifact and dependency scanners on
  release inputs and outputs.

## 9.6 Cache security and correctness

Provider caches may contain compiled device code or model-derived data. Every cache key must include enough identity to prevent incompatible reuse:

- model content hash;
- ORT version/build/flavor;
- provider and normalized options;
- provider SDK/runtime version;
- device architecture and relevant device/compiler identity;
- precision/dynamic-shape profile;
- wrapper cache schema version.

Requirements:

- application-private cache directory by default;
- atomic temp-file creation and rename;
- reject symlink/path traversal when sandbox mode is enabled;
- validate metadata before loading;
- invalidate on mismatch;
- do not treat cache contents as portable unless provider documentation guarantees it;
- provide clear/cache-size management APIs at a higher layer;
- do not log model-derived cache paths/keys if they reveal private information.

## 9.7 Logging, profiling, and privacy

Default logs may include:

- wrapper/shim/runtime versions;
- platform/architecture/flavor;
- provider state and normalized non-sensitive options;
- error code and redacted message;
- model identifier supplied by the caller or a non-reversible hash.

Default logs must not include:

- tensor contents;
- model bytes;
- user text/audio/images;
- arbitrary full paths;
- environment variables;
- credentials or provider SDK secrets;
- proprietary model metadata unless explicitly enabled.

Profiling is opt-in and may expose node/model names. On POSIX it writes only
under an existing absolute non-`/` application root owned by the effective
user, with owner read/write/search permission and no group/world write
permission. Fonix retains the root and randomized `0700` child by descriptor
and device/inode identity, reads the one bounded regular artifact without
following links, and publishes bytes only after checked non-recursive cleanup.
Replacement or incomplete cleanup returns neutral output, blocks reuse, and
retains the owner for an explicit finish retry. Other code executing as the
same effective user remains inside the application trust boundary.

No wrapper telemetry is enabled by default. Any future telemetry is application-controlled and documented separately from ORT/platform behavior.

## 9.8 Custom operators and plugin EPs

Custom code loading is an advanced attack surface. Initial policy:

- not exposed in the stable core API;
- explicit experimental sublibrary;
- absolute trusted application-local path;
- digest/signature allowlist support;
- exact runtime ABI compatibility;
- no loading from model-controlled paths;
- no unloading while sessions may reference the code;
- complete license and dependency inventory;
- separate Android single-runtime compatibility tests.

A plugin EP must not cause a second ORT runtime to be loaded.

## 9.9 Vulnerability response

Maintain:

- dependency inventory;
- ORT/provider version-to-artifact mapping;
- supported release branches;
- security contact and private reporting path;
- process to revoke compromised artifact digests;
- expedited runtime update path;
- compatibility regression suite including sherpa-onnx.

When a native security update is released:

1. Assess affected models/providers/platforms.
2. Pin the fixed release/source revision.
3. Rebuild all affected flavors.
4. Re-run platform/provider/sherpa tests.
5. Publish checksums, affected dependency information, and the advisory.
6. State whether applications must rebuild or only update a package.

Do not recommend substituting an ORT `.so`/DLL into an existing application without rebuilding/testing all native consumers.

## 9.10 Versioning model

Fonix versions the Dart package, native shim ABI, required ONNX Runtime API, and native artifact tuple separately. A Dart breaking change needs a migration note; an incompatible C boundary increments the shim ABI; raising the ORT API floor requires rerunning the sherpa compatibility matrix.

The package is pre-1.0 and `publish_to: none`. Dart API changes are reviewed through the diff, analysis, tests, and the reference application. The native C ABI keeps its canonical review record because packaged native libraries need an explicit compatibility boundary.

## 9.11 Release contents

A release includes only the target and provider tuples that were actually tested. Include:

- the Dart and shim source, exported with ordinary `git archive`;
- generated bindings and the native artifact lock;
- SHA-256 checksums for downloaded, provisioned, and distributed binary artifacts;
- the project license and applicable third-party licenses and notices;
- the supported target/provider matrix, minimum versions, migration notes, and known limitations; and
- the relevant build, package-audit, target-test, and signing results.

Do not publish a flavor whose binary origin, checksum, or license is unknown. A source export does not need a second manifest of hashes: Git already owns the tracked bytes and revision.

## 9.12 Release checklist

1. Review the selected ORT, sherpa, provider, Dart, Flutter, NDK, and platform versions.
2. Update the native lock only for an intentional artifact change.
3. Run analysis, package tests, binding regeneration, native tests, and the native ABI check.
4. Build and audit every package type that will be distributed.
5. Run every advertised target/provider composition on its required host or device.
6. Verify downloaded and final release artifact checksums.
7. Include the required licenses, notices, support matrix, and migration notes.
8. Sign, install, and exercise the artifacts through the chosen distribution channel.
9. Publish the source with ordinary `git archive` when the license or channel requires source delivery.

## 9.13 Maintenance and updates

For every dependency update, inspect the current upstream release and security notes, C API level, selected binary contents, provider compatibility, sherpa ownership, and platform toolchain requirements. Update the dated reference snapshot and rerun the affected package and target gates. “Latest” belongs in this review workflow, never in runtime resolution.

## 9.14 Integrity boundaries

Use SHA-256 where bytes cross a real artifact boundary: downloads, provisioned native runtimes, caches, external SDK inputs, final packages, and retained target evidence. Verify size and hash before promoting downloaded bytes into the artifact cache.

Use Git for tracked source and bundled test fixtures. Clean source copies are ordinary `git archive` exports of one clean revision. Do not maintain a repository-wide checksum manifest, hash a validator to validate another validator, or treat a self-consistent receipt bundle as proof that a target run occurred.

## 9.15 Publication decisions

The project owner chooses the advertised targets, distribution channel, signing identity, and release date. Those are ordinary release decisions, not a machine-generated approval protocol. Local static checks and simulator runs keep their narrow meaning and cannot be relabeled as physical-device, clean-machine, signing, or distribution evidence.
