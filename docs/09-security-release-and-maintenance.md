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
- Run artifact scanners/SBOM generation on release inputs and outputs.

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

Profiling is opt-in and writes only to an application-approved directory. Document that ORT/provider profiling may expose node/model names.

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
- complete license/SBOM entry;
- separate Android single-runtime compatibility tests.

A plugin EP must not cause a second ORT runtime to be loaded.

## 9.9 Vulnerability response

Maintain:

- dependency inventory/SBOM;
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
5. Publish checksums/SBOM/advisory.
6. State whether applications must rebuild or only update a package.

Do not recommend substituting an ORT `.so`/DLL into an existing application without rebuilding/testing all native consumers.

## 9.10 Versioning model

Track four independent versions:

1. Dart package semantic version.
2. Shim ABI integer/version.
3. Required ONNX Runtime C API floor.
4. Bundled/aligned ONNX Runtime and provider artifact versions.

Example diagnostics:

```json
{
  "dartPackage": "0.2.0",
  "shimAbi": 1,
  "requiredOrtApi": 27,
  "negotiatedOrtApi": 27,
  "runtimeVersion": "1.28.0",
  "artifactFlavor": "mobile",
  "runtimeOwner": "sherpa"
}
```

Rules:

- Dart breaking API change: package major bump after 1.0.
- Incompatible shim ABI: bump shim ABI and support explicit mismatch error; package version follows impact.
- Raising required ORT API: compatibility change requiring a release note and sherpa matrix update.
- Bundled ORT patch/minor update: package release with full regression/manifest change even if Dart API is unchanged.
- Provider SDK update: new flavor artifact and qualification evidence.

## 9.11 Release contents

A scoped pre-1.0 release may include only exact target/provider tuples that are
explicitly advertised and evidence-backed. Deferred Android QNN and Windows
target-host/provider/final-package/installer/clean-machine rows remain
unsupported and are not completion gates for that narrower release; their
source/static/security regressions remain mandatory. Unqualified
`Release-ready` and 1.0 retain the complete Tier-1/five-platform meaning and
therefore still require the deferred Windows lane.

Every release must include:

- Dart source and generated bindings;
- shim source/header;
- native artifact lockfile and checksums;
- license and third-party notices;
- SBOM for each binary flavor;
- supported platform/architecture/provider matrix;
- minimum OS/toolchain/application requirements;
- sherpa-onnx compatibility records;
- migration notes;
- benchmark/qualification summary with links to raw CI artifacts;
- known limitations;
- debug symbol retention/location policy.

Do not publish a flavor whose artifact hashes or dependency licenses are unknown.

## 9.12 Release checklist

1. Update dated upstream snapshot and determine whether compatibility floor changes.
2. Review ORT release security/breaking changes.
3. Refresh lockfile URLs/hashes/toolchains.
4. Regenerate bindings and verify shim ABI.
5. Rebuild every affected target/flavor.
6. Run every exact target matrix selected for advertising. Run the complete
   Tier-1 matrix for unqualified `Release-ready` or 1.0.
7. Run advertised EP qualification.
8. Run exact sherpa coexistence matrix and final Android artifact audit.
9. Generate SBOM/notices/checksums.
10. Scan artifacts for unexpected symbols/dependencies/secrets.
11. Build/install/run every advertised sample application from a clean
    environment.
12. Sign artifacts/packages as required.
13. Publish release notes with compatibility impact.
14. Archive manifests, build logs, symbols, and test evidence.

## 9.13 Maintenance/update procedure

At least for every intended package release and scheduled dependency review:

- inspect current official ORT release and C API version;
- inspect current sherpa Android build scripts and selected published artifacts;
- inspect relevant provider compatibility matrices;
- inspect Dart/Flutter FFI/build-hook changes;
- inspect Android NDK/AGP/page-size requirements;
- update `docs/12-reference-snapshot.md` with date and source revisions;
- decide whether to keep or raise the compatibility-floor API;
- maintain at least one overlap window when raising the floor, or publish a separate compatibility flavor;
- re-run all packaging/load-order tests before changing defaults.

“Latest” belongs in the update workflow, not in runtime/build resolution.

## 9.14 Offline deterministic audit evidence

`tool/ci/source_checksum_manifest.py` owns the closed source checksum manifest.
Generate `MANIFEST.sha256` only after the release-candidate tree has stopped
changing, then verify it without network access:

```bash
python3 -B tool/ci/source_checksum_manifest.py generate \
  --repository . --output MANIFEST.sha256
python3 -B tool/ci/source_checksum_manifest.py check \
  --repository . --manifest MANIFEST.sha256
```

Generation rejects unknown top-level entries, links, unsafe paths, special
files, and configured size/count overflows. Verification requires the exact
sorted manifest bytes and the exact current closed source file set; it is not a
best-effort check of only the paths already listed.
CI repeats verification from a freshly extracted `git archive HEAD`, so ignored
worktree state and source files that were never committed cannot satisfy the
release-evidence gate.

For one lock-selected native artifact, generate audit metadata and an SPDX 2.3
JSON SBOM from the resolver's staged directory:

```bash
python3 -B tool/ci/generate_release_sbom.py \
  --repository . \
  --staged-directory /path/to/fresh/resolver-output \
  --artifact-id onnxruntime-1.27.1-macos-arm64-cpu \
  --sbom-output /path/to/audit/fonix.spdx.json \
  --metadata-output /path/to/audit/fonix-release-audit.json
```

The generator is offline and deterministic. It rejects duplicate JSON keys,
unknown fields, lock/manifest drift, unsafe paths, links, extra staged files,
and staged byte size or hash drift. Its independent lock decoder also enforces
the exact schema-v2 Tier-1 CPU target set, OS-specific architecture and variant
combinations, artifact membership in that set, and equality between the shim's
required ORT API and the compatibility floor. A lock marked `release` must
cover every Tier-1 target with artifacts and record every provider's reported
name. Replacing a baseline row, introducing a non-CPU release flavor, or using
an architecture or variant from another platform is rejected before audit
metadata is emitted. Artifact declarations also have a closed selected-member
set at every archive depth: expected member paths and license IDs are unique,
notice depths cannot exceed the declared container chain, and every declared
symlink must resolve through an acyclic declared chain to an expected regular
file. The generator records the locked Dart dependency graph, exact ORT source
and compatibility inputs, selected artifact bytes, and notice identities
without embedding checkout or staging paths.

These files are unreleased audit metadata only. They are not release approval,
signing evidence, provider qualification, or authorization to publish or
distribute. Audit generation may succeed while readiness remains false. In
particular, the absent root `LICENSE` is a fail-closed readiness blocker; this
tool does not infer a Fonix license from the ONNX Runtime license or notices.
`--require-release-ready` therefore fails after writing the audit documents
until the separately owned release gates are established, and this command
must never be used to invent or bypass those gates.

`--require-release-ready` retains the unqualified Tier-1/five-platform meaning;
a scoped release must use a separately explicit advertised-scope approval and
must not reinterpret or weaken that flag.

## 9.15 Scoped pre-1.0 policy boundary

[`release/scoped-pre-1.0-v1.json`](../release/scoped-pre-1.0-v1.json) is the
machine-readable scope-only policy for the current CPU pre-1.0 work. Validate
it offline with:

```bash
python3 -B tool/ci/validate_scoped_release_scope.py \
  --repository . \
  --scope release/scoped-pre-1.0-v1.json \
  --output /path/to/new/scoped-scope-validation.json
```

The validator binds the policy to the exact native-lock bytes, package version,
shim ABI, required ORT API, all eight baseline targets, and each selected
locked artifact. The schema embeds the exact policy as its top-level `const`,
and the validator pins both the schema bytes and the two helper validators it
uses for native-lock and sherpa-lock semantics. The target array is canonical
and closed: iOS device, macOS
arm64, Android arm64-v8a, and Linux x86_64 are selected CPU candidates; iOS
simulator, Android x86_64, Linux arm64, and Windows x64 remain explicitly
unsupported. Android arm64 has distinct application-owned ORT 1.27.1 and
sherpa-owned process compositions; the latter binds `sherpa_onnx` 1.13.4 and
ORT 1.27.0 rather than inheriting the application-owned baseline. Only CPU
full-assignment may be advertised by this policy version. Android QNN and the
complete Windows target-host-through-clean-machine path are separate exact
deferred capabilities.

The output is deterministic, path-free validation metadata with the policy,
schema, validator, lock, package, exact selected-target, and five-composition
identities. It says `scope-only`; neither
the input nor output has a readiness, approval, signing, or publication field.
Selection therefore creates a bounded evidence worklist and nothing more. A
separate externally controlled candidate-approval manifest must bind the final
packages, target/runtime receipts, SBOMs, shared gates, and independent
licensing, security, signing, API/ABI, and publication approvals before a
scoped release can be called ready. That approval gate is distinct from, and
cannot weaken, global `--require-release-ready`.
