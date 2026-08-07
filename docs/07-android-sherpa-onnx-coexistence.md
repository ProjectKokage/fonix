# 7. Android Coexistence with sherpa-onnx

## 7.1 Goal

Allow a Flutter/Dart application to use both:

- sherpa-onnx through its Android JNI or Flutter FFI native libraries; and
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

Do not bake these numbers permanently into application logic. Record the exact
sherpa revision and selected publication/source artifact, then inspect its
native bytes in CI.

The exact Kokage dependency selection inspected on 2026-08-07 uses the
federated `sherpa_onnx` 1.13.4 Flutter packages rather than the source-build JNI
layout above. Its arm64-v8a and x86_64 package directories contribute raw
`jniLibs`: `libonnxruntime.so`, `libsherpa-onnx-c-api.so`, and
`libsherpa-onnx-cxx-api.so`. The C API depends on ORT; the C++ API depends on
both the C API and ORT. The compatibility tooling therefore treats `jni` and
`flutter-ffi` as separate closed library profiles. A source-tree default does
not override the inspected publication selected by the application lockfile.

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
sherpa Flutter FFI package/application:
  lib/<abi>/libsherpa-onnx-c-api.so
  lib/<abi>/libsherpa-onnx-cxx-api.so
  lib/<abi>/libonnxruntime.so

Dart wrapper external flavor:
  lib/<abi>/libfonix_shim.so
  (no libonnxruntime.so)
```

Link graph:

```text
libsherpa-onnx-c-api.so --DT_NEEDED--> libonnxruntime.so
libsherpa-onnx-cxx-api.so --DT_NEEDED--> libsherpa-onnx-c-api.so
libsherpa-onnx-cxx-api.so --DT_NEEDED--> libonnxruntime.so
libfonix_shim.so --dlopen/dlsym--> libonnxruntime.so
```

The legacy/source-built `jni` profile instead contains exactly
`libsherpa-onnx-jni.so`, which must depend on `libonnxruntime.so`. The two
profiles must never be mixed for one ABI or across the ABIs of one
compatibility record.

The shim must not have `DT_NEEDED` on ORT. It resolves `OrtGetApiBase`, obtains the version string, requests API 27 (in this snapshot), and retains the loader handle.

This mode lets either component load first. If the wrapper opens ORT first,
sherpa's `DT_NEEDED` must bind to the same SONAME object when its selected
native consumer loads. If sherpa loads first, the shim resolves the already
loaded/package-visible object. Both orders are mandatory tests.

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
4. Package one `libonnxruntime.so`, the selected sherpa native consumer set,
   the Dart shim, and matching provider dependencies. The current aligned-build
   driver produces the source-build JNI consumer.
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

The wrapper cannot rely on Maven/Gradle metadata to know the actual native
library selected. Gradle dependency verification can authenticate selected
dependency inputs against committed hashes, but it does not prove which native
bytes survived merging, stripping, and packaging. At runtime:

1. Open/resolve `libonnxruntime.so`.
2. Resolve `OrtGetApiBase`.
3. Copy `GetVersionString()` into diagnostics.
4. Request the compatibility-floor API.
5. Fail with `OrtApiIncompatibleException` if unsupported.
6. Query/provider-test the selected artifact rather than assuming XNNPACK/QNN/NNAPI exists.

The base shim uses C API 27 in this snapshot because the observed sherpa Android runtime is 1.27.x. A standalone ORT 1.28 artifact may still be used while the base shim remains on API 27. Features requiring API 28 must be separately gated; never index beyond the negotiated function table.

## 7.7 Native-input and final-artifact inspection

Inspect every selected publication/source native input—AAR, ZIP, or exact raw
`jniLibs` directory—and the final APK/AAB.

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
- `sherpa-audit` additionally requires an observed Fonix external shim to have
  its own SONAME and no ORT `DT_NEEDED`. It validates every observed JNI, C API,
  and C++ API sherpa consumer. With
  `--sherpa-library-profile jni|flutter-ffi`, it requires the exact selected
  profile across the aggregate input set and rejects missing, duplicate, or
  mixed consumers. Because it validates a shim only when one is present and
  does not bind source bytes to final bytes, this policy remains an input and
  preliminary inventory gate even when pointed at an APK/AAB; and
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

For the selected Flutter FFI publication, audit each exact `jniLibs` root (the
directory whose direct children are ABI directories) together with the
external Fonix native input:

```bash
python3 templates/android/verify_native_libs.py \
  --artifact /absolute/path/sherpa-arm64/android/src/main/jniLibs \
  --artifact /absolute/path/fonix-external-native-input \
  --policy sherpa-audit \
  --sherpa-library-profile flutter-ffi \
  --require-16k-page-alignment \
  --require-abi arm64-v8a \
  --reject-multiple-ort-owners \
  --reject-multiple-libcxx-owners
```

An explicit profile is mandatory for claim evidence. The default `auto` mode
is an inventory convenience and does not turn an input with no recognized
sherpa consumer into coexistence evidence.

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
exact sherpa publication was integrated, neither Dart-first nor sherpa-first was
exercised, no alternating speech workload ran, and no 16 KiB runtime was
tested.

When auditing multiple wrapper/sherpa/provider inputs, also pass `--reject-multiple-ort-owners` and `--reject-multiple-libcxx-owners`. Those checks operate across the named input artifacts; the standalone-final policy separately rejects ambiguous `libc++_shared.so` paths or unresolved ownership in each final package.

### Required assertions for sherpa-owned mode

- Exactly one input dependency owns `libonnxruntime.so` per ABI.
- The wrapper input artifact owns none.
- The final package has exactly one ORT path per ABI.
- The shim has no `DT_NEEDED: libonnxruntime.so`.
- The selected sherpa profile is closed: either one JNI consumer or the C API
  plus C++ API pair. Every required ORT/C-API dependency resolves in the final
  package.
- All included ABIs have the same logical runtime version/flavor.
- Provider companion libraries match that runtime.
- No accidental second Java ORT dependency contributes another native runtime.

### Required assertions for aligned mode

- The ORT hash equals the aligned build output recorded in the lockfile.
- Every selected sherpa native consumer links to the aligned SONAME graph.
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

The current standalone and sherpa-owned reference projects add a narrower
dependency-input checkpoint on top of that final-package rule. They commit
separate `gradle/verification-metadata.xml` files for the exact Release graphs
selected on the macOS gate host. Both gates require strict SHA-256 verification
of Gradle/Maven inputs. They remove the named inherited JVM-option variables
and verification-specific Gradle project-property override before setting a
gate-owned strict system property. They assume a non-hostile local Gradle user
home and init-script environment. A separate disposable staged copy replays
each exact Release graph, only after cache provisioning, with
`./gradlew --offline --no-daemon --dependency-verification strict assembleRelease bundleRelease`.
Gradle-wrapper/bootstrap resolution may contact configured repositories and
was not offline; the Flutter build invocations are not claimed offline.

The current metadata selects macOS AAPT2 artifacts only. A Linux or Windows
host needs a separately generated and reviewed metadata extension rather than
an unverified platform-executable substitution. This checkpoint is not binary
reproducibility, signing or distribution approval, Dart hosted-cache
authentication, or API 24, physical-device, installed AAB-split, performance,
or QNN evidence. It also does not weaken the final single-ORT and source-to-
final byte-binding gates below.

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

Android aligned/QNN device qualification is currently deferred until an exact
SDK, licensed backend, device, firmware, and redistribution decision are
available. The aligned-build and QNN receipt schemas, static linkage gates, and
tamper tests remain active contracts; passing them alone creates no QNN claim.

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

Compatibility evidence has three distinct, versioned layers:

1. The target harness emits a closed raw receipt conforming to
   [`load_order_receipt.schema.json`](../templates/android/load_order_receipt.schema.json)
   schema 2 for one exact APK, ABI, build type, load order, page-size
   environment, process, device, fixture set, workload, and lifecycle run.
2. [`validate_android_load_order_receipt.py`](../tool/ci/validate_android_load_order_receipt.py)
   independently rehashes and checks that raw evidence, then emits one closed,
   path-free validation record at schema 1. A raw receipt is not an input to the
   compatibility generator.
3. [`android_compatibility_manifest.py`](../tool/ci/android_compatibility_manifest.py)
   consumes only current-validator schema-1 records and emits the final Android
   compatibility record at schema 2.

Do not publish placeholder hashes or hand-author a validation record. The
compatibility generator independently revalidates the closed record contract
and requires identities for the current receipt schema, native verifier, and
validator. Those public hashes identify the toolchain; they are not a
signature and do not prove which process emitted a record.

This is an offline consistency gate, not a device-attestation system. Supplied
target and logcat JSON can be fabricated by an untrusted caller even when all
hashes agree. A target compatibility claim therefore also requires trusted
runner provenance for installation, `adb`/package-manager capture, the raw
logs, and the exact installed APK. The trusted target runner has now produced
independent `dart-first` and `sherpa-first` captures for one exact arm64-v8a
release-minified APK on the named 4 KiB and 16 KiB API 35 emulators. Validator
and compatibility-generator output remains offline consistency evidence even
after a trusted capture: both emitted layers carry
`claimStatus: offline-consistency-only`, and the validator record carries
`targetEvidenceProvenance: unverified`. Separately retained runner provenance
is the additional target layer; it exists for all four exact tuples, and the
runner does not rewrite either closed value in place.

### Raw target workload and lifecycle gate

The raw schema-2 receipt is intentionally stronger than two successful smoke
calls. It must prove all of the following:

- initialization events match the declared load order and the first native
  owner remains alive when the second becomes ready;
- 2--64 complete cycles strictly alternate Fonix then sherpa; every Fonix
  float32-le output equals the separately hashed reference bytes, while every
  sherpa step runs the closed single-thread 16 kHz/512-sample Silero VAD
  profile, submits exactly the padded audio, produces ordered non-overlapping
  segments within the separately hashed reference bounds, and drains its
  queue;
- Fonix cancellation is one accepted active native termination, settles once,
  publishes no output, and leaves no outstanding run before recovery;
- sherpa cancellation is honestly limited to a request between bounded VAD
  frames: no frame, flush, or segment is accepted/published afterward, and the
  detector is retired before recovery. It is not evidence of cancellation in
  the middle of a native sherpa call;
- one induced late Fonix completion is observed and suppressed under a newer
  authoritative generation, after which both Fonix inference and sherpa VAD
  recover against their references; and
- both `fonix-then-sherpa` and `sherpa-then-fonix` disposal orders pass,
  including double close/free, exact created/closed counts, zero pending runs
  and queued segments, and removal of every temporary profile root.

The validator checks bindings for a tuple-unique launch challenge and one
UID/PID across package-manager, target, and logcat evidence; the exact harness
contract and `pubspec.lock`; the release-mapped full sherpa source revision and
exact hosted provenance of the ABI-specific federated package; every Fonix and sherpa
model/input/reference fixture; and the final APK's exact ORT, external shim, C
API, and C++ API ELF graph. It does not independently observe those UID/PID or
device claims, or authenticate when the challenge was issued.

Run the validator once for each matrix tuple. The paths represented by
`<tuple>` must refer to evidence from that one launch:

```bash
python3 tool/ci/validate_android_load_order_receipt.py \
  --receipt /absolute/path/raw-<tuple>.json \
  --target-evidence /absolute/path/target-<tuple>.json \
  --logcat-evidence /absolute/path/logcat-<tuple>.json \
  --launch-challenge /absolute/path/challenge-<tuple>.bin \
  --final-apk /absolute/path/app-release.apk \
  --harness-contract /absolute/path/harness-contract.json \
  --pubspec-lock /absolute/path/pubspec.lock \
  --fonix-model /absolute/path/fonix-model.onnx \
  --fonix-input /absolute/path/fonix-input.bin \
  --fonix-reference-output /absolute/path/fonix-output.bin \
  --fonix-cancellation-model /absolute/path/fonix-cancel.onnx \
  --fonix-cancellation-input /absolute/path/fonix-cancel-input.bin \
  --sherpa-model /absolute/path/silero-vad.onnx \
  --sherpa-audio /absolute/path/vad-input.wav \
  --sherpa-reference /absolute/path/vad-reference.json \
  --sherpa-revision <full-40-character-commit> \
  --output /absolute/path/validated-<tuple>.json
```

The current validator deliberately accepts only a regular APK using
sherpa-owned/process runtime resolution and the `flutter-ffi` C API/C++ API
topology. It does not validate an AAB, a legacy JNI runtime composition, an
aligned runtime, QNN, ASR, or TTS. Use the separate static package-pair gate for
the exact APK and matching base-only AAB:

```bash
python3 -B tool/ci/android_static_package_manifest.py \
  --sherpa-source https://github.com/k2-fsa/sherpa-onnx \
  --sherpa-revision <full-40-character-commit> \
  --sherpa-artifact /absolute/path/sherpa-jniLibs \
  --wrapper-artifact /absolute/path/fonix-native-assets \
  --final-apk /absolute/path/app-release.apk \
  --final-aab /absolute/path/app-release.aab \
  --abi arm64-v8a \
  --ort-api-required 27 \
  --build-type release-minified \
  --snapshot-date YYYY-MM-DD \
  --output /absolute/path/static-package-pair.json
```

This gate requires one sherpa-owned ORT, the external Fonix shim, the exact
Flutter FFI C API/C++ API pair, a complete non-system dependency graph, 16 KiB
ELF compatibility, and source-to-final bindings for every selected library in
both packages. It rejects non-base AAB module sets and emits a deterministic,
path-free `static-package-only` record. It accepts no runtime receipt and does
not supersede the four-record APK runtime matrix. The preliminary
`sherpa-audit` policy alone is not this gate, and no AAB runtime claim exists
until a delivered split is installed and exercised.

Corrected commit-bound target checkpoint (2026-08-07): the source is commit
`8a9b6812c17237aeab7ec6d933f23932668c4b33`, with source-manifest SHA-256
`3728b31a38c246328cbeda7af14a7ba502489f6e89046371d2c22d29f264efd2`.
The Android sherpa gate, executed on a macOS arm64 host, provisioned all eight
exact qualification assets and audited the single-ORT graph in a 45,070,602-byte
API 35 arm64-v8a release-minified APK with SHA-256
`35cca3502504b07d2d21fd27cb46fd5833fab9c6337115adfd5a309818a28ac3`
and a 26,045,592-byte base-only AAB with SHA-256
`ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`.
The static package manifest and gate report have SHA-256 values
`99ccfb2f88eb16b9a7fdd3e03edf39c1529f771dedf210f5e481d85b3afd60e9`
and
`6ad082a3cdf3a498f88504e24a47eb6e239a0d62f13ea02e11b7fbe202e4764e`.

The same APK and harness contract with SHA-256
`5460d723423515d519819e6e369646e6f6cd70edb89760c02da133da40cb7f23`
then passed trusted-`adb` captures for both load orders on an API 35 arm64
4096-byte emulator (fingerprint hash
`acb4e14882d5e2e5cd4b91925de599cdc88a39b95d37a63ebe48c15f3000f384`)
and an API 35 `sdk_gphone16k_arm64` 16384-byte emulator (fingerprint hash
`d4cb1bb60eaee567df547e52dfdbbd5a1898d186aa9aa64f09c9b62a962f01ee`).
Every run used a distinct challenge, completed two alternating Fonix/Silero-VAD
cycles, and passed native Fonix cancellation, bounded between-frame sherpa
cancellation, stale completion suppression, recovery, both disposal orders,
double disposal, and zero pending work.

The capture-manifest, raw-receipt, and schema-1 validation SHA-256 values are:

- 4 KiB `dart-first`:
  `d5cf010cb70782bb9d542b0d3b68e8272b676d127f3c72679ad24c21ebb40821`,
  `bfc81d2e02e88c0223687cde15bb0b1df91bb2feea37c912bd729092ffc29bce`,
  `8c9da03a622cd6e97883396d6a9a8ff9ca5ee01c0ec477aaff12f6caf319c4ff`;
- 4 KiB `sherpa-first`:
  `6ab621e2f11ed7e3ce3b08b1bb60c1b228dc5f6385f6cabdf1685474fdb06abe`,
  `224cded3e1070074b98e3ee8b9c3b1296699e7e3606b0bdc70bc290cfbadf2ef`,
  `fc0f89c630b4ace1517e59db70fce6771b57a65a5040e151624666a01c4d501a`;
- 16 KiB `dart-first`:
  `56f13fa49e079c60a3deec3da4ed2068a3abe7a9409fe23d099dd87fa0b14e9c`,
  `0ecd917d2d0fe2b1e93e570dd5c802878d4f638c7f64e61f09b5eb0345d91067`,
  `82b811ba6f7f51e3793a76b4816e1d41e909478c3c41e21a33c8f6088f2b855f`;
- 16 KiB `sherpa-first`:
  `2c3371ce831eaae566c8abda5a6da9fe46ee857a23b919a5c69a4304ed646764`,
  `145f1d98440b896adfda283e5a4dde8a943ac2fda3de5b5210ba9268e943147d`,
  `e7255259522aec2c1fd8342ea68e1796d9a961f339127fdfc394d371cb91aa63`.

The four validation records passed the schema-2 aggregate with SHA-256
`0eb7fece2610b0225060696aa8599a960d01955cc1a7bc6f2ba8d7596964f504`.
Each capture manifest is retained separately as `trusted-adb-capture`; every
validator record and the aggregate remain `offline-consistency-only`. This
proves only the exact API 35 arm64-v8a release-minified APK matrix. It does not
prove API 24, a physical device, an AAB-derived installation, another
ABI/build, performance, signing, distribution, or general Android support.

### Static-to-final binding and matrix aggregation

The schema-2 compatibility generator accepts repeatable source
AAR/APK/AAB/ZIP inputs or exact extracted native-library directories, plus a
full sherpa commit SHA. A directory input must be the `jniLibs` root or another
root whose direct children are ABI directories; a whole pub-cache package
directory is not a native input. Its path-free identity is a domain-separated
hash of the closed loadable-library inventory. Package archive/content hashes
and the consuming `pubspec.lock` remain separate provenance.

The generator independently inventories every source input and the final
artifact, rejects duplicate logical owners, wrapper-owned ORT in external mode,
profile mixing, unexplained final native libraries, and unresolved
dependencies. Every selected source library, including provider companions and
`libc++_shared.so`, is tied to final architecture, SONAME, dependencies, and
file-backed `PT_LOAD` bytes. Only Flutter's exact `libapp.so` and
`libflutter.so` may lack a named source input, and only `libapp.so` may omit
`DT_SONAME`.

For the current arm64-v8a Release slice, provide exactly four independently
validated records—both load orders on both page sizes—to one APK compatibility
record:

```bash
python3 tool/ci/android_compatibility_manifest.py \
  --mode sherpa-owned \
  --sherpa-source https://github.com/k2-fsa/sherpa-onnx \
  --sherpa-revision <full-40-character-commit> \
  --sherpa-library-profile flutter-ffi \
  --sherpa-artifact /absolute/path/sherpa-arm64/android/src/main/jniLibs \
  --wrapper-artifact /absolute/path/fonix-external-native-input \
  --final-artifact /absolute/path/app-release.apk \
  --abi arm64-v8a \
  --ort-version-observed <exact-semver> \
  --ort-api-required 27 \
  --build-type release-minified \
  --snapshot-date YYYY-MM-DD \
  --load-order-validation-record /absolute/path/validated-dart-first-4k.json \
  --load-order-validation-record /absolute/path/validated-sherpa-first-4k.json \
  --load-order-validation-record /absolute/path/validated-dart-first-16k.json \
  --load-order-validation-record /absolute/path/validated-sherpa-first-16k.json \
  --output /absolute/path/compatibility-arm64-release.json
```

Every record must bind the same APK, application, harness, lockfile, sherpa
identity, fixtures, and runtime. Each launch challenge, target evidence,
logcat evidence, validation-record hash, and matrix tuple must be distinct.
Generate a separate four-record matrix for `debug` and another complete matrix
for each additional ABI. Cross-pairing two records or copying static package
metadata cannot satisfy the gate.

The generator retains contract-tested `aligned`/legacy-JNI parsing and the
separate schema-1 QNN qualification attachment, but the current load-order
validator cannot emit target records for those modes. Android aligned/QNN
target qualification is deferred. Synthetic/tamper tests and static aligned
build receipts must stay green, yet they do not support a compatibility or QNN
claim.

## 7.12 Test matrix

Target coexistence matrix. Complete one ABI/build artifact at a time; the
first development slice is arm64-v8a Release:

| Dimension | Cases |
|---|---|
| Build type | release/minified first; a separate complete debug matrix |
| ABI | arm64-v8a first; x86_64 only after its own complete matrix |
| Load order | Dart first, sherpa first |
| Runtime mode | sherpa-owned Flutter FFI shared runtime; aligned custom deferred |
| ORT compatibility | supported API, intentionally unsupported API |
| Provider | sherpa-owned CPU baseline; QNN aligned qualification deferred |
| Packaging | exact commit-bound APK/static evidence plus a passing 4 KiB/16 KiB four-record schema-2 matrix for one API 35 arm64-v8a release-minified APK; delivered-split runtime remains separate |
| Page size | 4 KB and 16 KB environment |
| Workload | 2--64 strict Fonix-reference/Silero-VAD cycles, starting with Fonix |
| Lifecycle | native Fonix cancellation settlement, between-frame VAD cancellation, stale suppression, recovery, both disposal orders, double disposal, zero pending work |

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
