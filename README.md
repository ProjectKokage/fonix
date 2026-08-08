# Fonix

Fonix is a local, explicit Dart and Flutter wrapper around the ONNX Runtime C
API. It owns a small C shim, validates every native boundary, keeps ONNX Runtime
ownership visible, and reports execution-provider discovery, registration, and
per-run assignment separately.

The package is currently `0.1.0-dev.1` and is intentionally marked
`publish_to: none`. The source implementation is usable for development, but
the repository does not yet contain the project-level licensing and signing
decisions needed for redistribution. The planned first publication is a scoped
pre-1.0 release over only exact, explicitly advertised target/provider tuples
that pass their own gates. It is not five-platform, unqualified
`Release-ready`, or 1.0 approval; those claims still require the complete Tier-1
matrix, including the deferred Windows target-host lane.

## What is implemented

- Runtime loading from a linked image, an application-local bundle, an
  application-owned process runtime, or a trusted absolute desktop path.
- Shim ABI validation, ONNX Runtime API-27 negotiation, exact runtime version
  reporting, immutable diagnostics, and process-lifetime loader retention.
- File and in-memory models, sandboxed external-data models, copied model
  metadata, and typed input/output metadata.
- Dense tensors for the practical ONNX scalar types, including strings,
  float16, and bfloat16, plus sequences, maps, optionals, and native-backed
  output leases.
- Synchronous named inference, cancellation, bounded provider profiling,
  worker-isolate sessions, and session pools bounded by both request count and
  aggregate retained input bytes.
- CPU, XNNPACK, CoreML, legacy NNAPI, QNN, and selected desktop execution-
  provider configuration. Provider availability is never treated as proof of
  node assignment.
- Deterministic build hooks, a hash-pinned native lockfile, final-package
  auditors, Android single-ORT ownership tooling, and reproducible release-
  evidence generators, plus a bounded public-API CPU target protocol covering
  the preserved serial path and a fixed two-worker session pool, five-launch
  raw collection, and independent offline replay validator.

Sparse tensors, opaque/custom values, arbitrary provider plugins, training,
GenAI, and Web/WASM are outside the current API.

## Minimal desktop example

Provision the exact ONNX Runtime library yourself and pass an absolute trusted
path. Fonix never downloads a runtime while the application is running.

```dart
import 'dart:typed_data';

import 'package:fonix/fonix.dart';

void main() {
  final runtime = OrtRuntime.open(
    source: OrtRuntimeSource.file(
      absolutePath: '/opt/my-app/lib/libonnxruntime.so',
      allowedRoot: '/opt/my-app/lib',
    ),
  );
  OrtSession? session;
  OrtTensor? input;
  OrtRunResult? result;
  try {
    session = OrtSession.fromFile(
      runtime: runtime,
      modelPath: '/opt/my-app/models/model.onnx',
      allowedRoot: '/opt/my-app/models',
    );
    input = OrtTensor.fromFloat32List(
      runtime: runtime,
      values: Float32List.fromList(<double>[1, 2, 3, 4]),
      shape: const <int>[1, 4],
    );
    result = session.run(inputs: <String, OrtTensor>{'input': input});
    final values = result.tensor('output').copyFloat32Data();
    print(values);
  } finally {
    result?.dispose();
    input?.dispose();
    session?.dispose();
    runtime.dispose();
  }
}
```

Synchronous native owners have idempotent `dispose()` methods;
`OrtIsolateSession` and `OrtSessionPool` have idempotent asynchronous `close()`
methods. Finalizers are a leak safety net, not the normal lifecycle mechanism.

## Runtime ownership

Choose one source mode deliberately:

| Source | Intended use |
| --- | --- |
| `OrtRuntimeSource.linked()` | Current linked profile: iOS arm64 device/simulator only |
| `OrtRuntimeSource.bundled()` | A lock-pinned runtime in the shim-owned application bundle |
| `OrtRuntimeSource.process()` | One application/process-owned runtime, including Android sherpa coexistence |
| `OrtRuntimeSource.file(...)` | A trusted absolute desktop runtime beneath an optional allow-root |

The native schema-3 build manifest contains the exact allowed source set. An
Android build additionally declares either `sherpa` or `application` as the
single runtime owner; conflicting sources and duplicate packaged ORT libraries
fail closed.

## Execution-provider evidence

Provider configuration is ordered. CPU, when explicit, must be last. Use
`OrtProviderRequirement.requireActive` or
`OrtProviderRequirement.requireFullAssignment` when a run must prove
assignment. Non-CPU reporting and strict fallback policies require an existing
absolute `artifactRoot`, where Fonix creates a private per-run profiling area.
On POSIX, that root must be a non-`/`, non-symlink directory owned by the
effective user, with owner read/write/search permission and no group/world
write permission.

```dart
final options = OrtSessionOptions(
  artifactRoot: '/opt/my-app/private/ort-artifacts',
  providers: <OrtExecutionProvider>[
    OrtExecutionProvider.coreMl(
      computeUnits: OrtCoreMlComputeUnits.cpuOnly,
      requirement: OrtProviderRequirement.requireActive,
    ),
    OrtExecutionProvider.cpu(),
  ],
  fallbackPolicy: OrtFallbackPolicy.report,
);
```

The result carries immutable run evidence. A provider being compiled,
discoverable, or successfully registered is not reported as active until a
validated ORT profile assigns at least one node to it. CoreML compute-unit
options express permission, not proof of GPU or Neural Engine execution.

## Native artifacts and Flutter packaging

`native/versions.lock.yaml` owns artifact identity, version, source, SHA-256,
platform, architecture, deployment floor, and notices. Build-time artifact
resolution accepts an explicitly provisioned cache or mirror, rehashes the
archive and staged bytes, and does not fall back to an unpinned download.

The standalone and sherpa-owned Android reference projects each commit Gradle
dependency-verification metadata for their exact macOS-hosted Release graph.
Their controlled gates require strict SHA-256 verification of Gradle/Maven
inputs. They remove the named inherited JVM-option variables and
verification-specific Gradle project-property override before setting a
gate-owned strict system property. The gates assume a non-hostile local Gradle
user home and init-script environment. The same exact Release graphs have a
separate disposable staged-copy replay, performed only after cache
provisioning with
`./gradlew --offline --no-daemon --dependency-verification strict assembleRelease bundleRelease`.
Gradle-wrapper/bootstrap resolution was not offline, and the Flutter build
invocations are not claimed offline. The committed metadata currently selects
macOS AAPT2 artifacts only; another build host needs separately generated,
reviewed metadata and evidence.

This checkpoint authenticates the pinned Gradle/Maven inputs consumed by those
two graphs. It is not binary reproducibility, signing or distribution
approval, Dart hosted-cache authentication, or API 24, physical-device,
installed-AAB-split, performance, or QNN evidence.

Apple applications must declare their own deployment floor and package the
generated manifest and `ThirdPartyNotices.txt`. See
[Build and Packaging](docs/05-build-and-packaging.md) for the hook settings and
asset-preparation command. Android applications integrating sherpa-onnx must
follow the one-runtime contract in
[Android sherpa-onnx Coexistence](docs/07-android-sherpa-onnx-coexistence.md).
The audit tooling distinguishes source-built JNI artifacts from the current
federated Flutter FFI layout (`libsherpa-onnx-c-api.so` plus
`libsherpa-onnx-cxx-api.so`) and accepts exact raw `jniLibs` directories as
source inputs. Static profile validation and source-to-final byte binding do
not replace target execution. The current evidence path takes one raw
schema-2 receipt from each exact APK run, validates it with
`tool/ci/validate_android_load_order_receipt.py`, and gives only the emitted
schema-1 validation records to `tool/ci/android_compatibility_manifest.py`.
Those offline records establish closed structure and byte/hash consistency;
they do not authenticate that target/logcat JSON came from `adb` or a real
installation. A target-support claim additionally requires trusted capture
provenance from the device runner. Both record layers therefore carry
`claimStatus: offline-consistency-only`; that status cannot be promoted by
supplying more self-authored JSON.
For one ABI and build this requires four independent records: the
`dart-first` and `sherpa-first` load orders on both 4 KiB and 16 KiB page-size
environments. The runtime validator currently covers a sherpa-owned Flutter
FFI APK only. The separate
`tool/ci/android_static_package_manifest.py` gate binds one exact APK and one
base-only AAB to the same selected raw sherpa/Fonix inputs, complete dependency
graph, and file-backed loaded segments. Its output is deliberately
`static-package-only`. In addition to synthetic/tamper coverage, the staged
runner has built and passed this gate for the committed arm64-v8a Release
sherpa reference composition. The corrected commit-bound pass also ran the
exact audited APK through trusted `adb` capture for `dart-first` and
`sherpa-first` on separate API 35 arm64 emulators with queried 4096-byte and
16384-byte page sizes. All four bounded runs passed and their schema-1 records
produced the required schema-2 compatibility aggregate. The validator and
aggregate remain `offline-consistency-only`; separately retained capture
manifests are the trusted-`adb` provenance layer. This exact four-tuple result
does not establish general Android support. The asset-free committed template
still emits
`unavailable/native-fixtures-unprovisioned`, and no AAB runtime claim exists
until an AAB-derived split is installed and exercised.

## Committed Flutter reference application

[`example/`](example/) is a shared iOS arm64, macOS arm64, Android
arm64-v8a, and Linux x86_64 Flutter reference application that imports only
`package:fonix/fonix.dart`. It uses the lock-selected linked runtime on iOS and
the bundled runtime on macOS, Android, and Linux, creates bounded worker-isolate
sessions, runs an exact CPU-assignment smoke model, and owns retry,
cancellation, suspension/resume, stale-result suppression, and idempotent
shutdown. Its Android one-shot path also has a closed XNNPACK functional
profile covering strict assignment, CPU parity, explicit fallback, recovery,
and cleanup. The fixtures are deliberately tiny and are not benchmarks.

The committed directory is a source template nested inside this package, so it
must be copied outside the checkout before Flutter builds its native assets.
The platform gates perform that copy, reproduce app-owned notices, analyze and
test the app, and audit the packaged bytes. The macOS and Android gates build
Release and respectively launch the final executable or install the final APK;
the Android gate also audits the AAB but does not install an AAB-derived split.
The iOS gate builds an unsigned arm64 device Release application and two
consecutive arm64 simulator Debug applications, then installs and exercises
only the audited simulator application. See the
[reference-app guide](example/README.md).

The Linux source includes a pinned Flutter scaffold, a relocatable
`$ORIGIN/lib` Release layout, a closed GNU-versioned 67-symbol shim export
surface, and an independent final-tree/ELF/provenance auditor. Its gate refuses
anything except the required Ubuntu 18.04.6 x86_64/glibc 2.27 host before
creating work, then builds, audits, and launches from an unrelated directory
under Xvfb with an environment-isolated private profile. That target-host gate
has not yet been run successfully; its presence and source-side synthetic tests are
source evidence, not Linux final-application evidence.

[`templates/android/sherpa_reference_app/`](templates/android/sherpa_reference_app/)
is the separate sherpa-owned scaffold. Its locked runner copies it outside the
checkout, guards all five hosted sherpa package trees and generated plugin
bindings, runs its host contract tests, builds an arm64-v8a R8 Release APK and
base-only AAB, and applies the closed package-pair audit. The bounded real
Fonix and sherpa qualification adapters, authoritative lifecycle publication
path, deterministic fixture generator, and trusted one-tuple target runner are
implemented. Both load orders now pass on 4 KiB and 16 KiB API 35 emulators
against one exact release-minified APK, and the four validated records pass
schema-2 aggregation.

## Verification

For an ordinary source check:

```sh
dart format --output=none --set-exit-if-changed .
dart analyze
dart test
python3 -B -m unittest discover -s tool/ci/tests -p 'test_*.py'
python3 -B -m unittest discover -s tool/tests -p 'test_*.py'
```

Real-runtime, cross-build, packaged-application, sanitizer, device, and
provider qualification paths are opt-in because they require exact external
artifacts or target hardware. [Validation](VALIDATION.md) records what was
actually executed and, equally importantly, what it does not prove.

Current evidence includes both a freshly generated final macOS arm64
application CPU run and the committed public-API reference application's exact
packaged CPU receipt, plus an exact ORT 1.27.1 CoreML `CPUOnly`
assignment/parity run. The committed Android arm64-v8a reference app also has
audited, development-signed R8 Release APK/AAB bytes and an exact
CPU/full-assignment APK receipt from an API 35 arm64 emulator with a queried
4096-byte page size. A separate Release build from the same source passed the
closed XNNPACK profile with one-node full assignment across six MatMul runs,
exact CPU parity, fallback report/rejection, recovery, and deterministic
cleanup. This is a functional emulator checkpoint, not physical-device,
performance, thermal, or provider-qualification evidence. The standalone path
still lacks API 24, 16 KiB, physical-device, AAB-split, and x86_64 execution.

The iOS arm64 reference source passes 72 application tests and analysis,
including 25/25 reference-smoke tests and 5/5 iOS project-contract tests, and
its simulator project has passed two consecutive linked Debug builds. The
complete manifest-bound device/simulator gate result is
`PASS (2026-08-07)`. The app delegate validates the exact native
smoke/challenge pair and forwards only a cached null-or-closed activation over
an argument-free app-owned channel; Dart revalidates it with a bounded wait and
never receives the raw environment. That gate is limited to an unsigned arm64
Release device `.app` build/static audit and an exact arm64 Debug simulator
CPU/full-assignment run. It cannot establish physical-device execution,
provisioning, approved device or distribution signing, an IPA or App Store
path, CoreML/XNNPACK/GPU/Neural Engine assignment, performance, or sustained
behavior. Its final
Mach-O audit can exclude separately packaged ORT Mach-Os and audited
load-command dependencies attributable to ORT, but cannot prove the absence of
runtime `dlopen`, another static ORT copy in a different Mach-O, or exactly-one
static archive linkage. Linux remains an active scoped-release gate. The
deferred Windows target-host gaps remain gates only for five-platform,
unqualified `Release-ready`, or 1.0 status.

The Linux x86_64 source path now has the committed reference scaffold,
cross-built GNU-versioned shim checks, and a fail-closed final-application gate
with focused synthetic coverage. This macOS arm64 host cannot build or execute
the final Linux x86_64 Flutter bundle, and no exact Ubuntu 18.04.6/glibc 2.27
gate report exists yet. The Linux support row therefore remains unpromoted.

The corrected sherpa-owned arm64-v8a checkpoint is bound to commit
`8a9b6812c17237aeab7ec6d933f23932668c4b33` and source manifest SHA-256
`3728b31a38c246328cbeda7af14a7ba502489f6e89046371d2c22d29f264efd2`.
The static gate produced a 45,070,602-byte API 35 release-minified APK with
SHA-256
`35cca3502504b07d2d21fd27cb46fd5833fab9c6337115adfd5a309818a28ac3`
and a 26,045,592-byte base-only AAB with SHA-256
`ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`.
The static package manifest, gate report, and harness contract have SHA-256
values
`99ccfb2f88eb16b9a7fdd3e03edf39c1529f771dedf210f5e481d85b3afd60e9`,
`6ad082a3cdf3a498f88504e24a47eb6e239a0d62f13ea02e11b7fbe202e4764e`,
and `5460d723423515d519819e6e369646e6f6cd70edb89760c02da133da40cb7f23`.

That same exact APK passed independent trusted-`adb` captures for both load
orders on an API 35 arm64 4096-byte emulator with fingerprint hash
`acb4e14882d5e2e5cd4b91925de599cdc88a39b95d37a63ebe48c15f3000f384`
and an API 35 `sdk_gphone16k_arm64` 16384-byte emulator with fingerprint hash
`d4cb1bb60eaee567df547e52dfdbbd5a1898d186aa9aa64f09c9b62a962f01ee`.
Every run used a distinct challenge and completed two alternating cycles,
native Fonix cancellation, between-frame sherpa cancellation, stale completion
suppression, recovery, both disposal orders, double disposal, and zero pending
work. The capture-manifest/raw-receipt/validation SHA-256 values are:

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

The four schema-1 validation records passed schema-2 aggregation; the
compatibility manifest has SHA-256
`0eb7fece2610b0225060696aa8599a960d01955cc1a7bc6f2ba8d7596964f504`.
Capture manifests retain `trusted-adb-capture` separately, while each validator
record and the aggregate remain `offline-consistency-only`. This proves the
exact API 35 arm64-v8a release-minified APK matrix only, not API 24, a physical
device, an AAB-derived installation, another ABI/build, performance, signing,
distribution, or general Android support.

The next target-evidence step is executing and calibrating the Linux x86_64
gate on its exact target host. The committed
[`scoped-pre-1.0-v1.json`](release/scoped-pre-1.0-v1.json) policy records the
exact CPU-only candidate and unsupported rows for scoped release work. A
`selected` row is a development target, not a support, readiness, signing, or
publication claim; every selected composition still needs its owning evidence
and an externally controlled bundle conforming to
[`scoped_release_approval.schema.json`](templates/ci/scoped_release_approval.schema.json).
The repository contains the fail-closed validator but no approved bundle or
publication authority. Evidence handling is closed and default-deny: a
referenced file that is present and passes its path, media-type, size, and
SHA-256 checks is present, not satisfied. Only evidence with a
repository-registered semantic contract can be satisfied; unregistered
evidence remains unvalidated and blocks readiness. The first registered
receipt contract is `macos-arm64-cpu-full-assignment-v1`, for the exact
`macos-arm64-cpu-full-assignment` record. Together with the separately derived
source-closure record, a fully populated synthetic bundle therefore reports 55
present, 2 satisfied, 53 unvalidated, and 0 missing evidence slots, and cannot
be ready. Exact receipt contents do not authenticate capture or target
execution and cannot replace the separate clean-machine, final-package,
signing, provider-qualification, support, or release gates.

Local development can proceed in parallel with
lifecycle/performance stabilization for those exact macOS, iOS-device, Linux,
and Android-without-QNN candidates. These are four selected OS target rows and
five CPU compositions: macOS arm64 bundled, iOS arm64 linked device, Linux
x86_64 bundled, and Android arm64 application-owned bundled plus sherpa-owned
process mode. The reference app now has a bounded, measurement-only
macOS/Linux CPU target protocol over a reproducible static-weight MatMul.
Protocol v3 preserves the prior synchronous serial field meanings and appends
an exact public `OrtSessionPool` phase with two protocol-v4 workers,
concurrency two, bounded input reservations, concurrent stabilization, three
two-lane full-isolate-roundtrip throughput windows, two strict CPU assignment
receipts outside timing, seven total-process RSS phases, zero accounting, and
idempotent close. Its host collector records five challenge-bound fresh
processes, preserves the raw target fragments and host observations, and
derives a collection-schema-3 record; the separate offline validator
independently reopens that raw bundle and emits only a schema-2
`offline-consistency-only` validation record. Existing protocol-v2 evidence
remains unchanged and cannot stand in for the pool fields. On macOS, the
collector reads the public `NSProcessInfo` thermal enum and current dynamic Low
Power boolean. Separately, bounded `pmset` output supplies the source-stable
active power source and matching opaque configured profile. The power label
pairs the closed source and API boolean with a domain-separated digest of that
profile; no `pmset` key is interpreted or compared with the API boolean.
API-reported Low Power `off` can also mean that the state was unknown or
unsupported. `baseline-comparable` means that the recorded controls are
complete and stable enough to match as input to a later review; it is not a
thermal-health judgment or baseline approval. Apple `nominal` can also mean
the thermal state was undetermined, stable elevated states still require
review, and the configured `pmset` profile does not assert every dynamic power
condition. Linux thermal observation remains unavailable. This source
checkpoint contains no controlled five-launch bundle from the new observer;
any later raw collection and replay are external evidence. This
capability does not create a controlled baseline, reviewed threshold, provider
qualification, support promotion, release approval, or non-macOS target
evidence. No final application has yet completed and replayed the required
five-launch protocol-v3 bundle. Android
QNN/aligned-runtime device qualification and all Windows target-host,
provider, final-app/package, installer, and clean-machine work are deferred
until their required SDK/hardware or Windows environment exists. Their
existing portable, static, source, tamper, cross-build, and loader-security
gates remain required.

The reviewed declaration boundaries are committed separately as
[`public-dart-api-v1.json`](release/public-dart-api-v1.json) and
[`native-c-abi-v1.json`](release/native-c-abi-v1.json). CI reconstructs the
Dart surface with Dart 3.11.5 and analyzer 14.1.0, and reconstructs the C
surface from `src/dort.h`, every platform export inventory, and the native
audit invocation contract. Any drift must be reviewed as an intentional
compatibility change before either canonical record is regenerated. These
records expose changes for review; they do not classify compatibility, prove
target behavior, or supply the independently controlled API/ABI approval
required for a scoped release.

## Documentation

- [Scope and Requirements](docs/01-scope-and-requirements.md)
- [Architecture](docs/02-architecture.md)
- [Dart API and Memory Model](docs/03-dart-api-and-memory-model.md)
- [Native C ABI](docs/04-native-c-abi.md)
- [Build and Packaging](docs/05-build-and-packaging.md)
- [Execution Providers](docs/06-execution-providers.md)
- [Android sherpa-onnx Coexistence](docs/07-android-sherpa-onnx-coexistence.md)
- [Testing, Benchmarking, and CI](docs/08-testing-benchmarking-and-ci.md)
- [Security, Release, and Maintenance](docs/09-security-release-and-maintenance.md)
- [Implementation Plan](docs/10-implementation-plan.md)
- [Decisions and Open Questions](docs/11-decisions-and-open-questions.md)
- [Support Matrix and Migration](docs/13-support-matrix-and-migration.md)

Treat models, metadata, provider profiles, external data, and native artifact
manifests as untrusted input. Fonix bounds and validates them, but callers still
own model provenance, application privacy policy, provider SDK licensing,
signing, and final-target qualification.
