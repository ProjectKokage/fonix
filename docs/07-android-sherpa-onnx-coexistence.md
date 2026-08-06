# 7. Android Coexistence with sherpa-onnx

## 7.1 Goal

Allow a Flutter/Dart application to use both:

- sherpa-onnx through its Android JNI/native libraries; and
- the Dart ONNX Runtime wrapper for independent models;

without packaging ambiguity, ABI mismatch, duplicate ORT runtimes, or silent loss of execution-provider capability.

## 7.2 Dated upstream observation

The sherpa-onnx `master` Android arm64 build script inspected on 2026-08-06:

- defaults `BUILD_SHARED_LIBS=ON`;
- documents that shared mode produces/uses both `libsherpa-onnx-jni.so` and `libonnxruntime.so`;
- defaults its Android ONNX Runtime version variable to `1.27.0`;
- accepts an externally supplied ORT through `SHERPA_ONNXRUNTIME_LIB_DIR` and `SHERPA_ONNXRUNTIME_INCLUDE_DIR`, or a root directory variable in shared mode;
- can enable QNN/RKNN through build options.

The semantic sherpa-onnx release `v1.13.4` (2026-07-07) records the update to ORT `1.27.0`. GitHub's later `xcframework` platform-package release (2026-07-31), currently marked `Latest`, references ORT `1.27.1` for Apple SPM. These observations show why the integration must inspect the selected Android artifact/source revision instead of assuming one repository-wide ORT version from a release badge.

Do not bake these numbers permanently into application logic. Record the exact sherpa revision/AAR and inspect it in CI.

## 7.3 Why duplicate packaging is unsafe

Android packages native libraries by ABI and filename. Two AARs that both contribute `lib/<abi>/libonnxruntime.so` do not create two selectable dependencies. They create a collision. Older tooling may choose one arbitrarily; modern tooling generally reports a merge error unless configured to pick one.

`pickFirst` only chooses a file. It does not prove that:

- sherpa was linked against the selected ORT ABI/build;
- provider companion libraries match;
- required symbols/API version exist;
- custom operators/build flags match;
- the chosen file is consistent across ABIs/build variants;
- future dependency ordering will choose the same binary.

Therefore:

> `jniLibs.pickFirsts += "**/libonnxruntime.so"` is prohibited.

The correct model has one declared owner and all consumers use that runtime.

## 7.4 Supported integration modes

### Mode A: sherpa-owned shared runtime (default coexistence)

Use when the selected sherpa artifact packages a shared `libonnxruntime.so` compatible with the wrapper's C API floor.

Packaging:

```text
sherpa AAR/application:
  lib/<abi>/libsherpa-onnx-jni.so
  lib/<abi>/libonnxruntime.so

Dart wrapper external flavor:
  lib/<abi>/libfonix_shim.so
  (no libonnxruntime.so)
```

Link graph:

```text
libsherpa-onnx-jni.so --DT_NEEDED--> libonnxruntime.so
libfonix_shim.so --dlopen/dlsym--> libonnxruntime.so
```

The shim must not have `DT_NEEDED` on ORT. It resolves `OrtGetApiBase`, obtains the version string, requests API 27 (in this snapshot), and retains the loader handle.

This mode lets either component load first. If the wrapper opens ORT first, sherpa's `DT_NEEDED` should bind to the same SONAME object when JNI loads. If sherpa loads first, the shim resolves the already loaded/package-visible object. Both orders are mandatory tests.

### Mode B: application-owned aligned runtime (advanced/preferred for custom EPs)

Use when:

- QNN or another custom EP is required;
- a newer/different ORT is required;
- reduced operators/custom ops are required;
- the stock sherpa artifact does not expose a compatible shared ORT;
- exact build control is required.

Process:

1. Pin and build one ORT source revision for every Android ABI with the desired EPs/options.
2. Build sherpa-onnx from the selected revision using that ORT's exact include and library directories.
3. Build the Dart shim against the compatibility-floor headers or exact aligned headers as specified by the shim ABI.
4. Package one `libonnxruntime.so`, the sherpa JNI library, the Dart shim, and matching provider dependencies.
5. Verify ELF dependencies, versions, hashes, provider registration, and both load orders.

The sherpa build script's external ORT variables are the intended integration
hook. The former illustrative script has been replaced by the fail-closed
[`sherpa_aligned_build.sh`](../templates/android/sherpa_aligned_build.sh)
driver. It accepts no positional fallback configuration and performs no
download. Its version-1 plan follows
[`sherpa_aligned_build_plan.schema.json`](../templates/android/sherpa_aligned_build_plan.schema.json).

The plan binds the full sherpa git commit, the script/output path for each ABI,
an ordered patch set, the NDK revision/host tag and hashes of
`source.properties`, Clang, and the CMake toolchain, plus the exact ORT C API
header set and shared-library identity for each ABI. Supply every local path
separately so the plan itself contains no host path:

```bash
templates/android/sherpa_aligned_build.sh \
  --plan /absolute/path/aligned-plan.json \
  --sherpa-source /absolute/path/local-sherpa-checkout \
  --ndk-root /absolute/path/android-ndk \
  --ort arm64-v8a=/absolute/path/ort-arm64 \
  --ort x86_64=/absolute/path/ort-x86_64 \
  --patch external-ort=/absolute/path/external-ort.patch \
  --work-dir /absolute/path/new-work-directory \
  --output /absolute/path/new-aligned-build-receipt.json
```

The checkout's `origin` and commit object must match exactly. The driver
archives that commit instead of using dirty working-tree bytes, applies and
rehashes the declared patches in order, disables sherpa/FetchContent ORT
acquisition, supplies only the declared external include/library directories,
and rejects a vendored, acquired, or emitted `libonnxruntime.so` or static ORT
archive. Success requires one 16-KiB-compatible sherpa JNI ELF per selected ABI
with the exact SONAME and exactly one `DT_NEEDED: libonnxruntime.so`. Copied JNI
outputs are placed under the new work directory and a path-free,
timestamp-free receipt records source, patches, NDK, headers, runtime ELF,
script, and JNI identities. It does not replace final APK or device evidence.

QNN is off unless the plan selects a QNN manifest hash/backend and both
`--qnn-manifest` and `--qnn-root` are present. The app-supplied manifest follows
[`qnn_sdk_manifest.schema.json`](../templates/android/qnn_sdk_manifest.schema.json)
and binds SDK/backend versions, HTTPS source, redistribution status, notice
identity, and every selected ABI artifact. Proprietary SDK bytes remain outside
this repository. A QNN-enabled aligned-build receipt is linkage/build evidence,
not provider qualification.

### Mode C: wrapper-owned standalone runtime

Use only when sherpa is absent or the selected sherpa integration does not package/use shared ORT and an explicit dual-runtime decision has been approved.

This is not the default coexistence path.

### Mode D: sherpa statically embeds ORT

When sherpa is built with `BUILD_SHARED_LIBS=OFF`, ORT may be statically linked into the sherpa JNI library. The Dart wrapper generally cannot obtain that internal API safely, especially when symbols are hidden.

Preferred resolution: rebuild sherpa in aligned shared mode.

A second wrapper-owned ORT would create two runtimes in one process. That exception is unsupported by default and requires a dedicated ADR and qualification covering memory, global/provider state, custom ops, load order, and shutdown. Do not infer safety from the absence of a filename collision.

## 7.5 Build configuration contract

Every Android build must declare its ONNX Runtime owner through the consuming
workspace `pubspec.yaml`. The sherpa-owned composition is:

```yaml
hooks:
  user_defines:
    fonix:
      android_runtime_owner: sherpa
      runtime_mode: external # optional; no other value is accepted
```

This emits only `libfonix_shim.so`. It compiles the native build identity
`android-owner-sherpa-source-process`, permits only the process runtime source,
and requests the fixed C API 27 floor at runtime. The Android owner macro also
makes the native source/profile gate reject explicit-file, bundled, and linked
runtime requests. `artifact_cache` and `artifact_mirror` are forbidden in this
mode, so a misspelled or stale artifact input cannot cause a wrapper-owned ORT
fallback.

The application-owned composition is:

```yaml
hooks:
  user_defines:
    fonix:
      android_runtime_owner: application
      runtime_mode: bundled # optional; no other value is accepted
      artifact_cache: /absolute/path/to/verified/offline/cache
```

`artifact_mirror` may replace or accompany `artifact_cache`. At least one must
contain the exact archive selected by `native/versions.lock.yaml`; the resolver
rehashes and audits it before emitting `libonnxruntime.so`. The shim embeds the
complete lock-selected artifact identity and a build ID of the form
`android-owner-application-source-bundled-artifact-<artifact-id>`.

At runtime, that bundled shim accepts only its own installed
`/data/app/.../*.apk!/lib/<closed-abi>/libfonix_shim.so` identity and asks
Bionic's application linker namespace for exactly `libonnxruntime.so`. It does
not canonicalize the APK member as a filesystem path and has no caller path,
working-directory, alternate-name, or global-path fallback. A malformed or
extracted shim location fails before any runtime lookup.

For both owners:

- `android_runtime_owner` is mandatory and closed to `sherpa` or
  `application`;
- the native-assets Android NDK API must be at least 24 for arm64-v8a and
  x86_64;
- an explicit contradictory `runtime_mode` is a build error;
- unknown Fonix user-define fields are a build error; and
- the native build manifest, runtime source diagnostics, staged artifact
  manifest, and final compatibility record remain separate evidence layers.

The currently locked application-owned artifacts are CPU baseline artifacts;
they are not QNN builds. To use application ownership alongside sherpa, rebuild
sherpa against that exact shared ORT and prove the result with the aligned
compatibility-manifest mode. The aligned-build driver accepts exact ORT roots
and an explicitly selected QNN SDK manifest, but the package hook does not
invent a custom QNN SDK, runtime root, provider library, or qualification
record.

Non-Android external, bundled, and linked profiles retain their existing
contracts. `android_runtime_owner` is rejected outside Android.

## 7.6 Runtime negotiation

The wrapper cannot rely on Maven/Gradle metadata to know the actual native library selected. At runtime:

1. Open/resolve `libonnxruntime.so`.
2. Resolve `OrtGetApiBase`.
3. Copy `GetVersionString()` into diagnostics.
4. Request the compatibility-floor API.
5. Fail with `OrtApiIncompatibleException` if unsupported.
6. Query/provider-test the selected artifact rather than assuming XNNPACK/QNN/NNAPI exists.

The base shim uses C API 27 in this snapshot because the observed sherpa Android runtime is 1.27.x. A standalone ORT 1.28 artifact may still be used while the base shim remains on API 27. Features requiring API 28 must be separately gated; never index beyond the negotiated function table.

## 7.7 AAR and final-artifact inspection

Inspect every selected AAR and the final APK/AAB.

### Inventory

For each ABI, record:

- path and owner artifact;
- SHA-256 of each `.so`;
- ELF class/machine;
- SONAME;
- `DT_NEEDED` entries;
- exported `OrtGetApiBase` symbol;
- shim exported `dort_*` allowlist;
- ORT/provider build metadata where available;
- load-segment alignment.

Use [`../templates/android/verify_native_libs.py`](../templates/android/verify_native_libs.py) and [`../templates/android/verify_elf_deps.sh`](../templates/android/verify_elf_deps.sh) as starting points.

The Python verifier has three explicit policy modes:

- `generic` inventories valid loadable paths, hashes, ELF identity, PT_LOAD layout, SONAME, and `DT_NEEDED` without inventing dependency expectations for an unknown producer;
- `sherpa-audit` additionally requires an observed Fonix external shim to have its own SONAME and no ORT `DT_NEEDED`, while an observed shared sherpa JNI library must depend on `libonnxruntime.so`; and
- `fonix-standalone-final` accepts only a final APK/AAB, requires exactly one ORT and Fonix shim per included/required ABI, enforces their closed SONAME and Android-system dependency sets, validates every counted library's SONAME and resolvable non-system dependencies, and rejects unexpected ORT dependencies or ambiguous `libc++_shared.so` ownership.

For the current standalone contract, `libonnxruntime.so` must depend on exactly `libandroid.so`, `libc.so`, `libdl.so`, `liblog.so`, and `libm.so`; `libfonix_shim.so` must depend on exactly `libc.so` and `libdl.so`. This is an artifact policy keyed to the selected builds, not a permanent Android-wide assumption.

Every final-package invocation must explicitly include `--require-16k-page-alignment`. The verifier rejects `--require-final-single-ort` and the standalone-final policy without that switch. For example:

```bash
python3 templates/android/verify_native_libs.py \
  --artifact build/app/outputs/flutter-apk/app-release.apk \
  --policy fonix-standalone-final \
  --require-16k-page-alignment \
  --require-abi arm64-v8a
```

Add `--require-abi x86_64` only when x86_64 is deliberately delivered in that
same final artifact; the committed standalone reference package is arm64-only.

This static gate proves PT_LOAD alignment and offset/virtual-address congruence in the inspected bytes. It does not replace the required 16 KB Android emulator/device run or final split-APK inspection.

Standalone checkpoint (2026-08-07): independent application-owned arm64 CPU
and XNNPACK profile builds passed their exact R8 Release APK/AAB audits. On an
API 35 arm64 emulator with a queried 4096-byte page size, the audited CPU APK
produced its full-CPU-assignment receipt; the audited XNNPACK APK produced one-
node full assignment for six static-weight MatMul runs across two sessions,
exact CPU parity, explicit fallback reporting and rejection, post-rejection
recovery, and deterministic cleanup. The XNNPACK result is a bounded functional
checkpoint, not physical-device, performance, thermal, or provider-
qualification evidence. Neither profile is sherpa coexistence evidence: no
exact sherpa AAR was supplied, neither Dart-first nor sherpa-first was
exercised, no alternating speech workload ran, and no 16 KiB runtime was
tested.

When auditing multiple wrapper/sherpa/provider inputs, also pass `--reject-multiple-ort-owners` and `--reject-multiple-libcxx-owners`. Those checks operate across the named input artifacts; the standalone-final policy separately rejects ambiguous `libc++_shared.so` paths or unresolved ownership in each final package.

### Required assertions for sherpa-owned mode

- Exactly one input dependency owns `libonnxruntime.so` per ABI.
- The wrapper input artifact owns none.
- The final package has exactly one ORT path per ABI.
- The shim has no `DT_NEEDED: libonnxruntime.so`.
- sherpa JNI's ORT dependency resolves in the final package.
- All included ABIs have the same logical runtime version/flavor.
- Provider companion libraries match that runtime.
- No accidental second Java ORT dependency contributes another native runtime.

### Required assertions for aligned mode

- The ORT hash equals the aligned build output recorded in the lockfile.
- sherpa JNI links to the aligned SONAME.
- shim linked/process mode matches the plan.
- QNN/provider libraries are the exact recorded versions.
- No default sherpa-downloaded ORT remains in an intermediate/final artifact.

## 7.8 Gradle policy

The application must not use native packaging collision rules to make the build pass. Keep the dependency graph intentional.

Illustrative Kotlin DSL policy:

```kotlin
android {
  defaultConfig {
    ndk {
      abiFilters += listOf("arm64-v8a", "x86_64")
    }
  }

  packaging {
    jniLibs {
      // Do not add pickFirsts for libonnxruntime.so.
      // Excludes are acceptable only when the excluded producer is known,
      // the remaining owner is verified, and the verification task enforces it.
    }
  }
}
```

Prefer selecting the wrapper's external flavor so there is nothing to exclude. Excluding an ORT from a generic wrapper AAR can be a temporary application-level bridge only if the wrapper's shim was built for runtime resolution and CI verifies the result.

Add a Gradle/CI task that consumes `MERGED_NATIVE_LIBS` or inspects final outputs. Dependency declarations alone are not evidence.

Use generic or `sherpa-audit` mode for intermediate/input inventories. A final APK/AAB command in either mode must add `--require-final-single-ort`, `--require-16k-page-alignment`, and the delivered ABI list. The standalone policy implies the single-ORT rule but still requires the explicit alignment switch and ABI list.

## 7.9 Loading behavior

The process-mode shim algorithm on Android should be equivalent to:

1. Try to locate an already loaded `OrtGetApiBase` where safely supported.
2. Try `dlopen("libonnxruntime.so", RTLD_NOW | RTLD_LOCAL)` in the application namespace.
3. Resolve `OrtGetApiBase` with `dlsym`.
4. Keep the handle alive for process lifetime.
5. Return a packaging/runtime-not-found error with ABI and mode if resolution fails.

An optional tiny Android bootstrap may call `System.loadLibrary("onnxruntime")` before loading the shim if device/toolchain testing proves it necessary. That bootstrap must remain a loading aid, not a second API binding, and both load orders still need tests.

Do not call `dlclose` while sherpa or any session may still use ORT.

## 7.10 Execution-provider implications

The wrapper can only use EPs compiled into/available to the shared runtime. In sherpa-owned mode:

- query/report available providers;
- do not advertise QNN merely because the Dart package has a QNN API;
- reject unavailable provider requests;
- qualify whether sherpa's stock ORT includes XNNPACK/NNAPI/QNN for the exact artifact;
- preserve CPU fallback observability.

For QNN, aligned mode is strongly preferred because the ORT/QNN SDK/backend tuple and provider libraries must match. Building a second QNN-enabled ORT for the wrapper is not acceptable.

### QNN qualification receipt gate

An aligned build does not establish that QNN accepted the graph. A QNN claim
requires a passed version-1 receipt conforming to
[`qnn_qualification_receipt.schema.json`](../templates/android/qnn_qualification_receipt.schema.json)
and a successful run of
[`validate_qnn_qualification_receipt.py`](../tool/ci/validate_qnn_qualification_receipt.py).
The validator rehashes the final APK, aligned-build receipt, QNN SDK manifest
and backend, model, QDQ configuration, input/reference fixtures, context cache,
and every evidence file. It also inventories the final APK and requires the
exact ORT, sherpa JNI shared-ORT dependency, and QNN backend for the device ABI.

The receipt binds the exact ORT/QNN/device/SOC/firmware/driver/provider-options
tuple. Per-node assignment evidence must assign every recorded node to
`QNNExecutionProvider`; `cpuNodes` is zero and the fallback policy is
`reject-cpu`. Cold and context-cache-hit runs must both equal the exact
reference output, record timings, and have identical hashes. The cache key is
derived from the model, QDQ, runtime, SDK/backend, provider options, SOC,
firmware, and driver identities; a separately bound mutation must produce a
different key and prove rejection of the stale cache. Dart-first and
sherpa-first evidence must bind the same APK, options, assignment, output, and
reference with their own timings.

The invocation is intentionally explicit because omitted evidence must not be
inferred:

```bash
python3 tool/ci/validate_qnn_qualification_receipt.py \
  --receipt /absolute/path/qnn-receipt.json \
  --aligned-build-receipt /absolute/path/aligned-build-receipt.json \
  --qnn-sdk-manifest /absolute/path/qnn-sdk-manifest.json \
  --qnn-root /absolute/path/qnn-sdk \
  --final-apk /absolute/path/app-release.apk \
  --model /absolute/path/model.onnx \
  --qdq-config /absolute/path/qdq.json \
  --input-fixture /absolute/path/input.bin \
  --reference-output /absolute/path/reference.bin \
  --assignment-evidence /absolute/path/assignment.json \
  --cold-evidence /absolute/path/cold.json \
  --cache-hit-evidence /absolute/path/cache-hit.json \
  --context-cache-artifact /absolute/path/context.bin \
  --invalidation-evidence /absolute/path/invalidation.json \
  --load-order-evidence dart-first=/absolute/path/dart-first.json \
  --load-order-evidence sherpa-first=/absolute/path/sherpa-first.json \
  --output /absolute/path/qnn-qualification.json
```

No validator output means no QNN qualification claim. Provider enumeration,
session creation, synthetic evidence, or a CPU-fallback result is not a
substitute.

## 7.11 Compatibility manifest

For each tested sherpa integration, store a record such as:

```yaml
sherpa_onnx:
  source: k2-fsa/sherpa-onnx
  revision: <commit-or-release>
  artifact_sha256: <sha256>
  android:
    abis: [arm64-v8a, x86_64]
    ort_owner: sherpa
    ort_version_observed: 1.27.0
    ort_api_required: 27
    ort_sha256_by_abi:
      arm64-v8a: <sha256>
      x86_64: <sha256>
    load_orders_tested: [dart-first, sherpa-first]
```

Do not publish placeholder hashes. The manifest belongs to CI evidence and should be regenerated when sherpa changes.

The repository-owned generator is
[`../tool/ci/android_compatibility_manifest.py`](../tool/ci/android_compatibility_manifest.py).
It accepts only exact regular AAR/APK/AAB inputs, a full sherpa commit SHA, and
target-host receipts. It independently inventories the input and final ELF
graphs, rejects wrapper-owned ORT in external mode, requires one ORT, sherpa
JNI library, and Fonix shim per declared ABI, and compares every final
file-backed `PT_LOAD` segment with its selected source. Whole-file hash changes
caused by removal of non-loaded debug data are recorded, but changes to loaded
bytes fail closed.

Generate one compatibility record per final build artifact. A release-minified
record can be produced along these lines after the application test harness has
emitted its receipts:

```bash
python3 tool/ci/android_compatibility_manifest.py \
  --mode sherpa-owned \
  --sherpa-source https://github.com/k2-fsa/sherpa-onnx \
  --sherpa-revision <full-40-character-commit> \
  --sherpa-artifact /absolute/path/sherpa.aar \
  --wrapper-artifact /absolute/path/fonix-external.aar \
  --final-artifact /absolute/path/app-release.apk \
  --abi arm64-v8a \
  --abi x86_64 \
  --ort-version-observed <exact-semver> \
  --ort-api-required 27 \
  --build-type release-minified \
  --snapshot-date YYYY-MM-DD \
  --load-order-receipt /absolute/path/receipt-1.json \
  --load-order-receipt /absolute/path/receipt-2.json \
  --output /absolute/path/compatibility.json
```

For application-owned aligned mode, pass `--mode aligned` and add
`--runtime-artifact /absolute/path/application-runtime.aar`. The sherpa input
must contain `libsherpa-onnx-jni.so` but no `libonnxruntime.so`; the separate
runtime artifact must be the sole source owner. The wrapper input likewise
contains only the Fonix shim. The generator rejects a default sherpa ORT left
in an aligned input and ties the application-owned runtime's loaded segments to
the single final ORT.

Only a record emitted by the QNN qualification gate may be attached with
`--qnn-qualification-record /absolute/path/qnn-qualification.json`, and only in
aligned mode. The generator binds its APK/build type, ABI, ORT hash/version/API,
zero-CPU assignment, and both load orders to the compatibility record. Without
that option, `android.qnnQualification` is `null`; aligned mode alone never
creates a QNN claim.

Receipts use the closed
[`load_order_receipt.schema.json`](../templates/android/load_order_receipt.schema.json)
schema version 1 and bind the final artifact hash, ABI, ORT hash and observed
version, required API, load order, build type, page size, bounded device
identity, test-harness hash, fixture hashes, and workload counts. For every
declared ABI, the generator requires the full Cartesian matrix of Dart-first
and sherpa-first at both 4 KiB and 16 KiB runtime environments; two
cross-paired receipts cannot satisfy the gate. Generate a
separate record for `debug`; a receipt for another build cannot satisfy the
selected final artifact. This gate deliberately cannot manufacture the missing
device evidence from static package metadata.

## 7.12 Test matrix

Minimum coexistence matrix:

| Dimension | Cases |
|---|---|
| Build type | debug, release/minified |
| ABI | arm64-v8a, x86_64 |
| Load order | Dart first, sherpa first |
| Runtime mode | sherpa-owned shared, aligned custom |
| ORT compatibility | supported API, intentionally unsupported API |
| Provider | CPU/XNNPACK baseline; QNN aligned where qualified |
| Packaging | APK and AAB/split install |
| Page size | 4 KB and 16 KB environment |
| Workload | Dart model run, sherpa ASR/TTS smoke, alternating/repeated runs |
| Lifecycle | sessions disposed in multiple orders; app activity restart/background cycle |

Stress tests should repeatedly create/dispose sessions and alternate sherpa/Dart inference to expose global state, thread, and allocator defects.

## 7.13 Failure messages

Failures must say what the application can do. Examples:

```text
The wrapper is configured for sherpa-owned ONNX Runtime, but
libonnxruntime.so could not be resolved for arm64-v8a. Verify that the
selected sherpa-onnx artifact uses shared ONNX Runtime and that the final
APK/AAB contains lib/arm64-v8a/libonnxruntime.so.
```

```text
Resolved ONNX Runtime 1.26.x, but the shim requires C API 27. Use a
compatible sherpa artifact or rebuild sherpa and the wrapper against one
aligned ONNX Runtime. Do not use Gradle pickFirst to substitute an
unverified library.
```

```text
QNN was requested, but the process-owned ONNX Runtime does not expose a
loadable QNN provider. Select the aligned QNN build and rebuild sherpa
against that exact runtime.
```

## 7.14 Prohibited workarounds

- `pickFirst`/`pickFirsts` for `libonnxruntime.so`.
- Renaming one ORT shared library and patching sherpa to load it merely to permit two runtimes.
- Copying a newer ORT into the APK without rebuilding/testing sherpa against it.
- Linking the process-mode shim to a different ORT at build time.
- Assuming all sherpa platforms use the same ORT version.
- Depending on native-library merge order.
- Hiding provider registration errors and falling back to CPU.
- Shipping a static-embedded sherpa ORT plus a wrapper ORT without an explicit dual-runtime qualification ADR.
