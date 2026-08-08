# Fonix Flutter reference application

This committed Flutter application demonstrates Fonix through
`package:fonix/fonix.dart` only. It loads the linked, lock-selected ONNX Runtime
on iOS and the bundled runtime on its macOS, Android, and Linux paths, creates
bounded worker-isolate sessions, runs the deterministic CPU smoke model,
exposes cancellation and retry, and reports path-free runtime and per-run
assignment evidence. Its bounded Android one-shot mode also exposes a closed
`xnnpack` profile for strict assignment, CPU parity, fallback, recovery, and
lifecycle evidence.

The app is a development reference for macOS arm64 at a 14.0 deployment floor
and Android arm64-v8a with a manifest/build floor of API 24. Its Linux x86_64
baseline is glibc 2.27. Its pinned Flutter iOS scaffold is arm64-only for both
device and simulator and declares a 15.1 deployment floor. It is not a release
artifact. The included 130-byte CPU model and 311-byte static-weight MatMul
assignment model are bounded functional fixtures, not representative
performance workloads. A separate generated
`[2048, 1024] @ [1024, 1024]` MatMul and its exact input/reference bytes are
measurement-only benchmark inputs; they do not establish a baseline or
threshold.

The Release target keeps the app sandbox and hardened runtime enabled but
declares `com.apple.security.cs.disable-library-validation`: the local gate has
no authorized Apple signing identity, so the app and Flutter framework are
ad-hoc signed with no common Team ID. A distribution build must use its
approved consistent signing identity and separately review or remove this
development entitlement; the macOS gate proves neither distribution signing
nor notarization. Android Release validation uses the local debug keystore and
is likewise development-only signing evidence. The iOS project commits no
development team or provisioning identity; simulator evidence cannot be
promoted into a signed device or distribution claim.

## External-copy requirement

The committed `example/` directory is a source template. Do not build it in
place: a consuming app's native-asset output must be outside the Fonix package
checkout, while this template is intentionally nested inside that checkout.
Each gate makes a bounded clean copy at a new absolute path and keeps every
generated native byte outside source. The Android gate first uses an external
hook for host tests, then selects application-owned bundled Android assets in
its copy. The iOS gate selects the linked runtime and 15.1 application-floor
hook only in its clean copy. The Linux gate changes only the local Fonix path
dependency: it retains the committed three-key bundled/cache/Apple-floor hook
configuration unchanged, ignores the Apple floor on the non-Apple target, and
regenerates the Linux-specific native manifest and notices in the external
copy.

On iOS, the app delegate accepts only `FONIX_REFERENCE_SMOKE=1` together with
an exact 64-character lowercase-hex `FONIX_REFERENCE_CHALLENGE`. It exposes
only `null` or `{schemaVersion: 1, challenge}` through the argument-free
`dev.fonix.reference/launch` channel, and Dart revalidates that closed shape
within five seconds. Partial or malformed activation fails closed with
`FONIX_REFERENCE_ACTIVATION_FAILURE`. Once activated, backend failures publish
one path-free `FONIX_REFERENCE_FAILURE=` payload; publication-transport
failures emit only `FONIX_REFERENCE_PUBLICATION_FAILURE`. No raw environment
map or smoke flag reaches Dart, and the environment-driven deterministic exit
path remains desktop-only on macOS and Linux. The iOS smoke remains resident
so the Flutter runner owns shutdown.

## Desktop CPU measurement fragment

An external macOS or Linux Release build accepts the exact opt-in activation
`FONIX_CPU_BENCHMARK=1`. Do not combine it with
`FONIX_REFERENCE_SMOKE=1`; the existing functional smoke retains startup
precedence. The benchmark path loads and verifies all four committed workload
assets before timing, then uses the synchronous public API with fixed
sequential CPU settings, one explicit intra-op thread, one explicit inter-op
thread, deterministic compute, and one reusable input tensor.

The runner requires bounded batch-median stabilization, records 100 warm
inference and native-to-Dart output-copy samples, completes three one-second
throughput windows whose cycle includes inference, output copy, exact-bit
validation, and result disposal, captures total-process RSS at eight lifecycle
phases, and performs one separate strict full-CPU-assignment run outside the
timed samples. Every output is compared bit-for-bit with the committed float32
reference. It then double-disposes every native owner and deletes the private
assignment-profile directory before publishing one bounded line beginning
with `FONIX_CPU_BENCHMARK_FRAGMENT=`.

That line is deliberately a `measurement-only-target-fragment`, not a
version-1 benchmark evidence receipt. A host-side collector must use repeated
fresh processes, bind the final application/runtime/build artifacts, add
controlled device, OS, power, thermal, and CPU-utilization observations, and
pass the result through the offline benchmark validator before review. The
fragment does not establish stable performance, a regression threshold,
CPU/provider qualification, platform support, release readiness, or
transferability.

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

## iOS arm64 gate

Provision both the exact iOS NuGet archive and the lock-selected macOS host
archive in one offline cache, then run the committed device-and-simulator gate:

```sh
python3 -B tool/ci/run_ios_reference_app_gate.py \
  --repository /absolute/path/to/fonix \
  --flutter /absolute/flutter/bin/flutter \
  --artifact-cache /absolute/verified/cache \
  --simulator-udid <canonical-ios-26.5-simulator-udid> \
  --work-dir /absolute/new/fonix-ios-reference-gate
```

The iOS input is
`microsoft.ml.onnxruntime.1.27.1.nupkg`, exactly 135,152,698 bytes with
SHA-256
`9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383`.
The gate pins macOS 26.5.2 (25F84), Xcode 26.6 (17F113), the 26.5 device and
simulator SDKs, Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` (Flutter
`3.47.0-0.1.pre`), and an arm64-capable iPhone 17 Pro simulator running iOS
26.5. The exact bundle identifier is `dev.fonix.fonixReference`. The app and
hook floor is 15.1; the exact final-binary floors are 15.1 for Runner and the
shim and 15.0 for App and Flutter.

The frozen source checkpoint passed all 72 application tests and analysis;
`reference_smoke_test.dart` passed 25/25 and
`ios_project_contract_test.dart` passed 5/5. Two consecutive linked simulator
Debug builds also passed. The complete unsigned-device Release `.app`
build/static audit and simulator install, receipt, settlement, and cleanup gate
is `PASS (2026-08-07)`.

The device branch produces and audits an unsigned arm64 Release application.
Its root application and executable have no signature or provisioning profile;
the three nested frameworks have exact teamless ad-hoc signatures. That branch
is static-only evidence and is not executed without a physical device. The
simulator branch builds Debug twice and audits the exact tree before
installation. Its install-transport identity preserves every directory, file
path, byte, and the `Runner` executable bit while permitting `simctl` to clear
the executable bit only on the exact App, Flutter, and Fonix framework
binaries. It then binds that installed identity to a fresh 256-bit challenge
and process, requires ORT 1.27.1 CPU output `[1,4,9,16,25,36]`, full assignment
and double close, settles the process, and uninstalls the app. Its result is
exact-simulator functional evidence only.

For both branches, the closed Mach-O inventory and load-command audit excludes
a separately packaged raw ONNX Runtime Mach-O and an audited ORT load-command
dependency. Hook metadata, embedded schema-3 identity, dyld exports/fixups, and
normalized runtime fields bind the packaged shim to its prepackage output
through the explicitly accounted transformations. This does not prove the
absence of runtime `dlopen`, another static ORT copy in a different Mach-O, or
exactly-one static archive linkage. It also does not prove physical-device
execution, approved signing/provisioning, IPA/App Store distribution,
CoreML/XNNPACK/GPU/Neural Engine qualification, performance, or transferability
to another tuple.

## Linux x86_64 gate

Provision the exact lock-selected Linux x86_64 archive and run the gate on the
required Ubuntu 18.04.6 (Bionic) x86_64 host with glibc 2.27:

```sh
/usr/local/bin/python3.11 -I -S -B tool/ci/run_linux_reference_app_gate.py \
  --repository /absolute/path/to/fonix \
  --flutter /absolute/flutter/bin/flutter \
  --artifact-cache /absolute/verified/cache \
  --pub-cache /absolute/offline/pub-cache \
  --work-dir /absolute/new/fonix-linux-reference-gate
```

The cache must contain `onnxruntime-linux-x64-1.27.1.tgz`, exactly 8,828,892
bytes with SHA-256
`25b1ef1fea1acd210d63f8f24dc870ad6e077795ce1f54876252c6d3803c15af`.
The gate requires Flutter `3.47.0-0.1.pre` at revision
`bd1e75d918605c91b411e8789fb911e6c9a84534`, Python 3.11.9 invoked with
`-I -S -B`, Clang/LLVM 10.0.0, CMake
3.22.1, Ninja 1.10.2, GNU binutils 2.30, pkg-config 0.29.1, GTK 3.22.30,
and the Ubuntu `xvfb` package `2:1.19.6-1ubuntu4.15` owning
`/usr/bin/Xvfb`. Alternate absolute tool paths can be supplied through the
gate's explicit tool arguments, except that the Xvfb package/path identity is
part of the target profile. This is a required profile, not evidence that the
tuple has already passed.

Before creating its work directory, the gate rejects every foreign or newer
host. On the exact host it freezes one source-manifest epoch, creates verified
tool aliases, and forces both shim and runner links through the bound LLVM 10
`ld.lld` using `-fuse-ld=lld`. It makes the external app copy, verifies the
offline archive, and
resolves the enforced lockfile twice from only the supplied offline pub cache.
It regenerates Linux assets, analyzes and tests the app, performs a clean
re-resolution, and produces a Release bundle. The independent
auditor then binds the final no-link tree to the hook input/output and compiler
identities, checks the exact native-asset mappings and notices, audits every
ELF's architecture, SONAME, dependencies, RUNPATH, interpreter, symbol-version
floors, GNU build ID, RELRO/NOW/NX/PIE properties, and enforces the exact
67-symbol `FONIX_DORT_1.0` shim surface. Finally, the gate launches the packaged
executable under the verified Xvfb server directly (without a wrapper) from an
unrelated working directory with a positive-allowlist private environment,
requires one exact CPU/full-assignment receipt, settles
the complete process group, rejects residual private state, and proves the
application and frozen-source trees remained unchanged.

No target-host PASS is recorded in this source snapshot. The focused auditor
and gate tests and the foreign-host refusal have run on macOS arm64, but that
cannot establish a Linux build, loader, GTK, glibc-floor, or inference claim.

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

The clean copy retains
`android/gradle/verification-metadata.xml`. The gate requires strict SHA-256
dependency verification for both Flutter package builds. It removes the named
inherited JVM-option variables and verification-specific Gradle
project-property override before setting a gate-owned strict system property,
and assumes a non-hostile local Gradle user home and init-script environment.
A separate direct Gradle check uses a disposable staged copy after cache
provisioning and runs
`./gradlew --offline --no-daemon --dependency-verification strict assembleRelease bundleRelease`.
Gradle-wrapper/bootstrap resolution was not offline, and the Flutter build
invocations are not claimed offline. The committed graph currently includes
only the macOS AAPT2 artifact; it is not valid evidence for a different build
host.

This verifies Gradle/Maven dependency inputs only. It is not binary
reproducibility, distribution signing or approval, Dart hosted-cache
authentication, API 24 or physical-device execution, an installed
AAB-derived split, performance, or QNN evidence.

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

The Android and complete iOS gates are currently explicitly provisioned local
package/runtime gates rather than hosted CI lanes. Their auditor and
orchestration tamper tests remain part of the ordinary Python CI collection.

The widget/controller tests use an app-owned fake backend. They do not load
native code and therefore prove application state ownership, cancellation,
retry, terminal-backend replacement, stale-result suppression,
suspend/resume oscillation, synchronous-listener reentrancy, and disposal
behavior—not native cancellation latency or another platform/provider claim.
