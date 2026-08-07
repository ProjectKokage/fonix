# Fonix

Fonix is a local, explicit Dart and Flutter wrapper around the ONNX Runtime C
API. It owns a small C shim, validates every native boundary, keeps ONNX Runtime
ownership visible, and reports execution-provider discovery, registration, and
per-run assignment separately.

The package is currently `0.1.0-dev.1` and is intentionally marked
`publish_to: none`. The source implementation is usable for development, but
the full five-platform release matrix is not yet qualified and the repository
does not yet contain the project-level licensing and signing decisions needed
for redistribution.

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
  worker-isolate sessions, and bounded session pools.
- CPU, XNNPACK, CoreML, legacy NNAPI, QNN, and selected desktop execution-
  provider configuration. Provider availability is never treated as proof of
  node assignment.
- Deterministic build hooks, a hash-pinned native lockfile, final-package
  auditors, Android single-ORT ownership tooling, and reproducible release-
  evidence generators.

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
sherpa reference composition. The latest runtime-provisioned pass also ran the
exact audited APK through trusted `adb` capture for `dart-first` and
`sherpa-first` on the same API 35 arm64 emulator with a queried 4096-byte page
size. Both bounded runs passed; their validator records remain
`offline-consistency-only`, and the missing 16 KiB pair still blocks matrix
aggregation and a general coexistence claim. The asset-free committed template
still emits
`unavailable/native-fixtures-unprovisioned`, and no AAB runtime claim exists
until an AAB-derived split is installed and exercised.

## Committed Flutter reference application

[`example/`](example/) is a shared macOS arm64 and Android arm64-v8a Flutter
reference application that imports only `package:fonix/fonix.dart`. It loads the
lock-selected bundled runtime, creates bounded worker-isolate sessions, runs an
exact CPU-assignment smoke model, and owns retry, cancellation,
suspension/resume, stale-result suppression, and idempotent shutdown. Its
Android one-shot path also has a closed XNNPACK functional profile covering
strict assignment, CPU parity, explicit fallback, recovery, and cleanup. The
fixtures are deliberately tiny and are not benchmarks.

The committed directory is a source template nested inside this package, so it
must be copied outside the checkout before Flutter builds its native assets.
The platform gates perform that copy, reproduce app-owned notices, analyze and
test the app, build Release, audit the packaged bytes, and then launch the final
macOS executable or install the final Android APK. The Android gate also audits
the AAB, but does not install an AAB-derived split. See the
[reference-app guide](example/README.md).

[`templates/android/sherpa_reference_app/`](templates/android/sherpa_reference_app/)
is the separate sherpa-owned scaffold. Its locked runner copies it outside the
checkout, guards all five hosted sherpa package trees and generated plugin
bindings, runs its host contract tests, builds an arm64-v8a R8 Release APK and
base-only AAB, and applies the closed package-pair audit. The bounded real
Fonix and sherpa qualification adapters, authoritative lifecycle publication
path, deterministic fixture generator, and trusted one-tuple target runner are
implemented. Both 4 KiB load orders now pass against one exact Release APK;
executing both load orders on a real 16 KiB environment is the next target
layer.

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
performance, thermal, or provider-qualification evidence. API 24 execution, an
actual 16 KiB runtime, physical-device behavior, AAB split installation,
x86_64 execution, and QNN remain unproved. Sherpa coexistence has the
historical exact-artifact 4 KiB runs below, but they cannot promote support
because their source provenance was defective. iOS, Linux, and the deferred
Windows target-host gaps remain explicit release gates.

The historical sherpa-owned arm64-v8a checkpoint was built while the checkout
reported commit `0e4effef837352685ab5ac39cd28e017973fca70`; its
`MANIFEST.sha256` file had SHA-256
`e0080a0ed914fd55b5ab3e02f2593ab2c9a1c490ebf0e2129d9c2a806acfc5d6`.
The static gate produced a 45,070,602-byte R8 Release APK with SHA-256
`93fd4a388a723621b404aabfc1026197d8fb11620fb2f3d2746bac594fd81e54`
and a 26,045,592-byte base-only AAB with SHA-256
`ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`.
The static package manifest and gate report have SHA-256 values
`a75a3f7f7004a86453cf9d6d6f686c1e5645eaf167dd44268b67d107a3b099d3`
and
`b63dd44133b8a74baa02d810e9e54905d6732a4f62e6099cf56c3bc90f28fd14`.

The same APK and harness contract
(`963e76730336bf9c22e2641d0a28b066a2e9592d64eef2723d80b81da7217519`)
then passed two independent trusted-`adb` captures on one API 35 arm64 emulator
with page size 4096 and fingerprint hash
`acb4e14882d5e2e5cd4b91925de599cdc88a39b95d37a63ebe48c15f3000f384`.
The runs used distinct launch challenges. Both completed two alternating
cycles, native Fonix cancellation, between-frame sherpa cancellation, stale
completion suppression, recovery, both disposal orders, double disposal, and
zero pending work; the package was absent after each run. The `dart-first`
capture-manifest/raw-receipt/validation hashes are respectively
`6eb2a5df4ef1b2b541522f1448cfde27ddae705d5fd810f717956a945792ad40`,
`fe72465d43b0981ca6b4d6fad18f4c63579b833369a85656c1e89a34bcb357c8`,
and `44a27782535653538616556cabbbe918e0c0efec299315e182b5b0a9fb0f1293`;
the `sherpa-first` hashes are
`2171e8eaf7685c5bcb59cf68195ddfc81fd23372926df45809f1c8c0a58815d0`,
`414b85ecf40598ec9433f9c48232d634d087b0901e34ae1e76b0c06309cd4f1b`,
and `72f52d31edce29f5bdbba8cfbdc664043d45324e90b23780fe2124588bb45dbd`.
The capture manifests record `trusted-adb-capture`; the schema-1 validation
records remain `offline-consistency-only` and do not authenticate themselves.
This is an exact 4 KiB emulator checkpoint, not API 24, 16 KiB, physical-device,
AAB-derived-install, performance, distribution, or general Android support
evidence.

These exact APK/static/target results remain factual, but only as historical
exact-artifact evidence. Commit `0e4effe` omitted required handwritten
`lib/src/build/native_versions_lock.dart`, and its manifest included generated
ignored state. No source-final, commit-bound, support-promotion, or
reproducibility claim may be derived from this checkpoint.

The next development slice is to rebuild from a corrected committed tree and
rerun `dart-first` and `sherpa-first` on both 4 KiB and 16 KiB environments,
then aggregate those four fresh records. After that come the iOS arm64
reference app, Linux x86_64 target-host package, and cross-platform
lifecycle/performance and release stabilization. Android
QNN/aligned-runtime device qualification and all Windows target-host,
provider, final-app/package, installer, and clean-machine work are deferred
until their required SDK/hardware or Windows environment exists. Their
existing portable, static, source, tamper, cross-build, and loader-security
gates remain required.

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
