# Fonix Flutter reference application

This committed Flutter application demonstrates Fonix through
`package:fonix/fonix.dart` only. It loads the bundled, lock-selected ONNX
Runtime, creates bounded worker-isolate sessions, runs the deterministic CPU smoke
model, exposes cancellation and retry, and reports path-free runtime and
per-run assignment evidence. Its bounded Android one-shot mode also exposes a
closed `xnnpack` profile for strict assignment, CPU parity, fallback, recovery,
and lifecycle evidence.

The app is a development reference for macOS arm64 at a 14.0 deployment floor
and for Android arm64-v8a with a manifest/build floor of API 24. It is not a
release artifact. The included 130-byte CPU model and 311-byte static-weight
MatMul assignment model are bounded functional fixtures, not representative
performance workloads.

The Release target keeps the app sandbox and hardened runtime enabled but
declares `com.apple.security.cs.disable-library-validation`: the local gate has
no authorized Apple signing identity, so the app and Flutter framework are
ad-hoc signed with no common Team ID. A distribution build must use its
approved consistent signing identity and separately review or remove this
development entitlement; the macOS gate proves neither distribution signing
nor notarization. Android Release validation uses the local debug keystore and
is likewise development-only signing evidence.

## External-copy requirement

The committed `example/` directory is a source template. Do not build it in
place: a consuming app's native-asset output must be outside the Fonix package
checkout, while this template is intentionally nested inside that checkout.
Each gate makes a bounded clean copy at a new absolute path, patches only the
local Fonix dependency and its platform hook configuration, and keeps every
generated native byte outside source. The committed `pubspec.yaml` remains the
macOS-default source template; the Android gate first uses an external hook for
host tests, then selects application-owned bundled Android assets in its copy.

## macOS gate

Provision the exact lock-selected archive and reference dylib, then run:

```sh
python3 -B tool/ci/run_macos_reference_app_gate.py \
  --repository /absolute/path/to/fonix \
  --flutter /absolute/flutter/bin/flutter \
  --artifact-cache /absolute/verified/cache \
  --reference-runtime /absolute/verified/libonnxruntime.1.27.1.dylib \
  --work-dir /absolute/new/fonix-reference-gate
```

`--work-dir` must be an absolute path that does not yet exist. Use Flutter
revision `bd1e75d918605c91b411e8789fb911e6c9a84534`. The gate works offline
after provisioning: it verifies and copies the exact archive, reproduces the
committed manifest/notices, resolves packages offline, analyzes and tests the
app, builds Release, independently audits the final application, and then
launches the packaged executable in bounded one-shot mode.

After a successful gate, the copied app remains at `--work-dir`. To inspect the
interactive UI with the already resolved inputs:

```sh
cd /absolute/new/fonix-reference-gate
flutter run -d macos --no-pub
```

## Android arm64-v8a gate

Provision the exact lock-selected Android NuGet archive, OpenJDK 21.0.12,
Android command-line tools 20.0, build-tools 36.0.0, NDK 28.2.13676358, and
bundletool 1.18.3. Then run:

```sh
python3 -B tool/ci/run_android_reference_app_gate.py \
  --repository /absolute/path/to/fonix \
  --flutter /absolute/flutter/bin/flutter \
  --artifact-cache /absolute/verified/cache \
  --work-dir /absolute/new/fonix-android-reference-gate \
  --android-sdk /absolute/Android/sdk \
  --java-home /absolute/openjdk-21.0.12 \
  --bundletool /absolute/bundletool-all-1.18.3.jar \
  --avd-name api35-arm64-avd
```

The default `--smoke-profile cpu` preserves the original CPU receipt. Run the
separate XNNPACK checkpoint with the same inputs and a new work directory:

```sh
python3 -B tool/ci/run_android_reference_app_gate.py \
  --repository /absolute/path/to/fonix \
  --flutter /absolute/flutter/bin/flutter \
  --artifact-cache /absolute/verified/cache \
  --work-dir /absolute/new/fonix-android-xnnpack-gate \
  --android-sdk /absolute/Android/sdk \
  --java-home /absolute/openjdk-21.0.12 \
  --bundletool /absolute/bundletool-all-1.18.3.jar \
  --avd-name api35-arm64-avd \
  --smoke-profile xnnpack
```

The gate is pinned to the same Flutter revision as the macOS gate. It analyzes
and tests the app, builds R8-minified Release APK and AAB bytes for arm64-v8a,
and independently audits both packages before installation. The audit verifies
the closed manifest/module set, development-signing identity, exact
manifest/notices/models, four-library inventory, single ORT ownership,
dependencies/exports, static 16 KiB ELF alignment for both artifacts, and APK
`zipalign -P 16`. It compares the packaged stripped ORT's ordered loaded
segments with the lock-selected source runtime rather than assuming their
whole-file hashes remain equal.

With `--avd-name`, the audited APK is installed on the exact named API 35 arm64
emulator and must produce the receipt selected by `--smoke-profile`. The CPU
profile requires its unchanged full-assignment/double-close receipt. The
XNNPACK profile requires one-node full assignment for six MatMul runs over two
sessions, exact CPU parity, a CPU-only fallback report and strict rejection,
post-rejection recovery, five total sessions, double close, and removed profile
roots. Failure paths also retire non-empty private roots without replacing the
authoritative error. Omit `--avd-name` for package evidence only. The successful
local receipts used a queried 4096-byte page size; the XNNPACK result is a
functional checkpoint, not a benchmark or provider qualification. Neither
profile validates API 24 execution, an actual 16 KiB runtime, a physical
device, x86_64, sherpa coexistence, QNN, or an installed AAB-derived split.

## Maintain the committed native assets

When the lock-selected macOS artifact changes, place the exact archive in the
ignored template cache and regenerate the two app-owned source assets:

```sh
cd /absolute/path/to/fonix/example
mkdir -p .fonix-artifact-cache
cp /absolute/verified/cache/onnxruntime-osx-arm64-1.27.1.tgz \
  .fonix-artifact-cache/
flutter pub get --offline
dart run fonix:fonix_prepare_flutter_assets \
  --target-os macos \
  --architecture arm64 \
  --variant default \
  --package-root /absolute/path/to/fonix \
  --cache /absolute/path/to/fonix/example/.fonix-artifact-cache \
  --output /absolute/path/to/fonix/example/assets/fonix
```

The preparation command must reproduce the two committed app-owned files in
`assets/fonix/` exactly. It does not download. Run the complete external-copy
gate afterward because publication is a two-file operation and Flutter
packaging happens later.

The Android sidecar pair lives in
`assets/fonix/android-arm64-v8a/`. The Android gate regenerates those two files
from the exact nested NuGet/AAR bytes, compares them byte-for-byte with the
committed pair, and only then publishes them as the generic Flutter asset paths
inside its external copy. Do not hand-edit either platform's generated pair.

The application starts its worker lazily, keeps at most one run outstanding,
invalidates stale completions before cancellation or shutdown, and recreates
the worker after suspension or a terminal failure. Closing is graceful: it is
not a hard deadline when an execution provider does not cooperate with ONNX
Runtime termination.

CI additionally builds the Release macOS `.app`, runs the independent Mach-O,
signature, native-identity, notice, and packaged C probe audit, and then
launches a bounded one-shot smoke mode from the packaged executable. It checks
the exact numeric output and CPU assignment receipt and closes the worker twice
to prove idempotence.

The Android gate is currently an explicitly provisioned local package/runtime
gate rather than a hosted CI lane. Its auditor and orchestration tamper tests
remain part of the ordinary Python CI collection.

The widget/controller tests use an app-owned fake backend. They do not load
native code and therefore prove application state ownership, cancellation,
retry, terminal-backend replacement, stale-result suppression,
suspend/resume oscillation, synchronous-listener reentrancy, and disposal
behavior—not native cancellation latency or another platform/provider claim.
