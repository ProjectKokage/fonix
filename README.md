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

## Committed Flutter reference application

[`example/`](example/) is a shared macOS arm64 and Android arm64-v8a Flutter
reference application that imports only `package:fonix/fonix.dart`. It loads the
lock-selected bundled runtime, creates a bounded worker-isolate session, runs an
exact CPU-assignment smoke model, and owns retry, cancellation,
suspension/resume, stale-result suppression, and idempotent shutdown. The
fixture is deliberately tiny and is not a benchmark.

The committed directory is a source template nested inside this package, so it
must be copied outside the checkout before Flutter builds its native assets.
The platform gates perform that copy, reproduce app-owned notices, analyze and
test the app, build Release, audit the packaged bytes, and then launch the final
macOS executable or install the final Android APK. The Android gate also audits
the AAB, but does not install an AAB-derived split. See the
[reference-app guide](example/README.md).

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
4096-byte page size. This does not prove API 24 execution, an actual 16 KiB
runtime, physical-device behavior, AAB split installation, x86_64 execution,
XNNPACK, QNN, or sherpa coexistence. iOS, Linux, and the deferred Windows
target-host gaps remain explicit release gates.

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
