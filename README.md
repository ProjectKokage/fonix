# Fonix

Fonix runs [ONNX Runtime](https://onnxruntime.ai) models from Dart and Flutter.
It loads a pinned ONNX Runtime library through a small C shim, gives every
native object an explicit owner, and tells you which execution provider
actually ran your model instead of which one you asked for.

```dart
final result = session.run(inputs: {'input': tensor});
print(result.tensor('output').copyFloat32Data());
```

Fonix is version `0.1.0-dev.1`, unpublished (`publish_to: none`), and licensed
under the [GNU General Public License v3](LICENSE).

## Contents

- [What it does](#what-it-does)
- [Platforms](#platforms)
- [Add Fonix to an app](#add-fonix-to-an-app)
- [Run a model](#run-a-model)
- [Run without blocking the UI](#run-without-blocking-the-ui)
- [Execution providers](#execution-providers)
- [Diagnostics](#diagnostics)
- [Working on Fonix](#working-on-fonix)
- [Documentation](#documentation)

## What it does

- **Models:** load from a file or from bytes, including models with external
  data files. Input and output names, types, shapes, and model metadata are
  readable before you run anything.
- **Values:** dense tensors of every common ONNX element type (including
  strings, `float16`, and `bfloat16`), plus sequences, maps, and optionals.
- **Runs:** synchronous runs on the calling isolate, or asynchronous runs in a
  worker isolate, singly or as a fixed-size pool. Runs can be cancelled.
- **Execution providers:** CPU, XNNPACK, CoreML, NNAPI, QNN, and the desktop
  GPU providers (CUDA, TensorRT, DirectML, OpenVINO, oneDNN, MIGraphX). Fonix
  reads ONNX Runtime's profile to report which provider ran each node.
- **Packaging:** a build hook compiles the shim and stages the exact ONNX
  Runtime build named in [`native/versions.lock.yaml`](native/versions.lock.yaml),
  checked by SHA-256. Nothing is downloaded while the app runs.
- **Android with sherpa-onnx:** an app that already ships sherpa-onnx can let
  Fonix use sherpa's copy of ONNX Runtime, so the process contains exactly one.

Not supported: sparse tensors, custom operators, arbitrary provider plugins,
training, ONNX Runtime GenAI, and the web.

## Platforms

All rows use ONNX Runtime 1.27.1 (C API 27). "Runtime" is where the ONNX
Runtime library comes from by default.

| Target | Minimum | Runtime | Tested so far |
| --- | --- | --- | --- |
| iOS arm64 device and simulator, x86_64 simulator | 15.1 | Linked into the app | arm64 Simulator inference; device builds are audited but not run |
| macOS arm64 | 14.0 | Bundled with the app | Packaged-app inference |
| Android arm64-v8a, x86_64 | API 24 | Bundled, or shared with sherpa-onnx | arm64 emulator inference (API 35), including alongside sherpa-onnx |
| Linux x86_64, arm64 | glibc 2.27 | Supplied by the app, or bundled | Cross-built and audited, not run |
| Windows x64 | Windows 10 | Supplied by the app, or bundled | Cross-built, not run |

Nothing has been run on a physical device yet. The
[support matrix](docs/13-support-matrix-and-migration.md) lists the exact
evidence and what is still missing for every row and provider.

## Add Fonix to an app

### 1. Depend on it

Fonix is not on pub.dev. Use a path or Git dependency:

```yaml
dependencies:
  fonix:
    path: ../fonix
```

### 2. Fetch the pinned ONNX Runtime archives

The build hook never downloads. Put the archives your targets need into a
directory (the *artifact cache*), using the file names from the lock file's
`source.url` entries:

| Targets | Archive |
| --- | --- |
| iOS, Android | `microsoft.ml.onnxruntime.1.27.1.nupkg` |
| macOS arm64 | `onnxruntime-osx-arm64-1.27.1.tgz` |
| Linux x86_64 | `onnxruntime-linux-x64-1.27.1.tgz` |
| Linux arm64 | `onnxruntime-linux-aarch64-1.27.1.tgz` |
| Windows x64 | `onnxruntime-win-x64-1.27.1.zip` |

The hook rejects any archive whose SHA-256 differs from the lock file. Only
targets that link or bundle the runtime need an archive; a sherpa-owned Android
build or an external desktop build needs none.

### 3. Configure the build hook

Settings go under `hooks.user_defines.fonix` in the app's `pubspec.yaml`. For
example, an app targeting iOS, macOS, and Android that shares ONNX Runtime
with sherpa-onnx on Android:

```yaml
hooks:
  user_defines:
    fonix:
      android_runtime_owner: sherpa
      artifact_cache: /absolute/path/to/fonix-artifact-cache
      application_minimum_os: '15.1'
```

| Setting | Meaning |
| --- | --- |
| `android_runtime_owner` | Required for Android. `sherpa` uses sherpa-onnx's `libonnxruntime.so`; `application` bundles Fonix's own. |
| `artifact_cache` / `artifact_mirror` | Directory holding the archives from step 2. Read only when a target stages a runtime. |
| `application_minimum_os` | Required for iOS and macOS. Must match or exceed the lock file's minimum (iOS 15.1, macOS 14.0) and must not exceed your Xcode deployment target. |
| `runtime_mode` | `linked`, `bundled`, or `external`. Usually omitted: the defaults are linked on iOS, bundled on macOS arm64 and application-owned Android, and external elsewhere. |
| `process_runtime_basenames` | Desktop apps that ship their own ONNX Runtime. See [Build and Packaging](docs/05-build-and-packaging.md#flutter-application-contract-for-shared-settings-apple-floors-and-notices). |

Unknown settings fail the build.

### 4. Package the manifest and notices (iOS and macOS)

Apple apps must ship the runtime's build manifest and third-party notices as
Flutter assets. Generate them before `flutter build`:

```sh
dart run fonix:fonix_prepare_flutter_assets \
  --target-os macos \
  --architecture arm64 \
  --variant default \
  --cache /absolute/path/to/fonix-artifact-cache \
  --output /absolute/path/to/app/assets/fonix
```

```yaml
flutter:
  assets:
    - assets/fonix/fonix-native-artifact-manifest.json
    - assets/fonix/ThirdPartyNotices.txt
```

### 5. Android with sherpa-onnx

With `android_runtime_owner: sherpa`, Fonix ships no ONNX Runtime of its own
and loads sherpa's at run time. The app must contain exactly one
`libonnxruntime.so` per ABI; never resolve a duplicate with Gradle
`pickFirst`. Read
[Android sherpa-onnx Coexistence](docs/07-android-sherpa-onnx-coexistence.md)
before changing an Android build.

## Run a model

Open a runtime, create a session, run it, and dispose of everything you
created. `OrtRuntimeSource` says where the runtime comes from and must match
the build hook's runtime mode:

| Source | Use on |
| --- | --- |
| `OrtRuntimeSource.linked()` | iOS |
| `OrtRuntimeSource.bundled()` | macOS arm64, application-owned Android, bundled Linux or Windows |
| `OrtRuntimeSource.process()` | sherpa-owned Android, or a desktop app that loads its own runtime |
| `OrtRuntimeSource.file(absolutePath: ..., allowedRoot: ...)` | Desktop, with a runtime library at a known absolute path |

```dart
import 'dart:typed_data';

import 'package:fonix/fonix.dart';

void main() {
  final runtime = OrtRuntime.open(source: const OrtRuntimeSource.bundled());
  OrtSession? session;
  OrtTensor? input;
  OrtRunResult? result;
  try {
    session = OrtSession.fromFile(
      runtime: runtime,
      modelPath: '/absolute/path/to/model.onnx',
      allowedRoot: '/absolute/path/to',
    );
    input = OrtTensor.fromFloat32List(
      runtime: runtime,
      values: Float32List.fromList(<double>[1, 2, 3, 4]),
      shape: const <int>[1, 4],
    );
    result = session.run(inputs: <String, OrtTensor>{'input': input});
    print(result.tensor('output').copyFloat32Data());
  } finally {
    result?.dispose();
    input?.dispose();
    session?.dispose();
    runtime.dispose();
  }
}
```

Rules worth knowing:

- **Dispose explicitly.** Runtimes, sessions, tensors, and results own native
  memory. `dispose()` is safe to call twice; using an object after it throws.
  Finalizers exist only as a leak safety net.
- **Data is copied by default.** `copyFloat32Data()` and friends return Dart
  copies, so they stay valid after the result is disposed. For zero-copy input,
  allocate an `OrtNativeBuffer`.
- **Paths are checked.** With `allowedRoot`, a model path outside that
  directory is rejected.
- **Errors are typed.** Failures throw subclasses of `OrtException` such as
  `OrtModelLoadException` or `OrtRunException`, carrying ONNX Runtime's code and
  message.

See [Dart API and Memory Model](docs/03-dart-api-and-memory-model.md) for
every value type, metadata, and native buffers.

## Run without blocking the UI

`session.run` blocks the calling isolate. In a Flutter app, run inference in a
worker isolate with `OrtIsolateSession`. The worker opens its own runtime and
session; inputs and outputs are copied across.

```dart
final worker = await OrtIsolateSession.spawn(
  runtimeSource: const OrtRuntimeSource.linked(),
  model: OrtModelSource.file(absolutePath: modelPath),
);
try {
  final result = await worker.run(
    inputs: <String, OrtIsolateValue>{
      'input': OrtIsolateTensor.fromFloat32List(
        values: Float32List.fromList(<double>[1, 2, 3, 4]),
        shape: const <int>[1, 4],
      ),
    },
  );
  print(result.tensor('output').copyFloat32Data());
} finally {
  await worker.close();
}
```

- `worker.startRun(...)` returns an `OrtIsolateRun` you can `cancel()`.
- Runs on one worker are queued; `maxPendingRuns` and
  `maxOutstandingInputBytes` bound the queue and fail fast with
  `OrtWorkerQueueFullException`.
- `OrtSessionPool.spawn(size: n, ...)` starts `n` workers with the same model
  for parallel runs.

## Execution providers

List providers in order of preference. CPU, if listed, must be last.

```dart
final options = OrtSessionOptions(
  artifactRoot: '/absolute/private/dir/ort-artifacts',
  providers: <OrtExecutionProvider>[
    OrtExecutionProvider.coreMl(
      computeUnits: OrtCoreMlComputeUnits.all,
      requirement: OrtProviderRequirement.requireActive,
    ),
    OrtExecutionProvider.cpu(),
  ],
  fallbackPolicy: OrtFallbackPolicy.report,
);
final session = OrtSession.fromFile(
  runtime: runtime,
  modelPath: modelPath,
  options: options,
);
```

A provider can be compiled in, registered successfully, and still run no
nodes. Fonix only calls a provider *active* when ONNX Runtime's profile shows
it ran at least one node; `result.providerEvidence?.activeProviderIds` lists
them. To get that evidence, set `artifactRoot` to a private directory Fonix
can write profiles into.

Every listed provider must register, or session creation fails; Fonix never
drops an unavailable provider silently. The requirement adds a check on each
run:

| `OrtProviderRequirement` | The run also fails when |
| --- | --- |
| `preferred`, `required` | (no extra check) |
| `requireActive` | the provider ran no nodes |
| `requireFullAssignment` | any node ran on another provider |

`OrtFallbackPolicy` decides what happens when nodes land elsewhere: `allow`
and `report` continue (`report` records the reason), `rejectCpu` fails on CPU
fallback, and `rejectAny` fails on any fallback.

CoreML compute units say what CoreML *may* use; they do not prove that the GPU
or Neural Engine ran anything. See
[Execution Providers](docs/06-execution-providers.md) for each provider's
options and limits.

## Diagnostics

`runtime.diagnostics` and each run result's `diagnostics` describe exactly
what is loaded: wrapper and shim versions, the negotiated ONNX Runtime API, the
runtime version string, where the runtime came from, the locked artifact, and
each provider's state and normalized options. `toJson()` gives a redacted map
that is safe to log; it never contains model contents, tensors, or private
paths.

## Working on Fonix

Read [`AGENTS.md`](AGENTS.md) before changing code. The ordinary local checks
need only Dart and Python:

```sh
dart format --output=none --set-exit-if-changed .
dart analyze --fatal-infos
dart test
python3 -B -m unittest discover -s tool/ci/tests -p 'test_*.py'
python3 -B -m unittest discover -s tool/tests -p 'test_*.py'
```

Tests that need a real ONNX Runtime, a device, or provider hardware are skipped
unless their inputs are provided. [`VALIDATION.md`](VALIDATION.md) records the
latest local run and what it did not cover.

[`example/`](example/) is a Flutter reference app used by the platform gate
scripts in `tool/ci/`; it also has an opt-in CPU benchmark. Copy it outside
this checkout before building it. See the
[reference-app guide](example/README.md).

Repository layout:

| Path | Contents |
| --- | --- |
| `lib/` | Public Dart API (`package:fonix/fonix.dart`) |
| `src/` | C shim (`dort_*` functions) and its header |
| `hook/` | Build hook: shim compilation and runtime staging |
| `native/versions.lock.yaml` | Pinned ONNX Runtime artifacts and hashes |
| `tool/ci/` | Package auditors and platform gate scripts |
| `example/` | Flutter reference app |
| `templates/` | Android sherpa-onnx reference app and CI templates |
| `docs/` | Design and contract documents |

## Documentation

Start with the [documentation index](docs/README.md). The most useful pages for
app developers:

- [Dart API and Memory Model](docs/03-dart-api-and-memory-model.md)
- [Build and Packaging](docs/05-build-and-packaging.md)
- [Execution Providers](docs/06-execution-providers.md)
- [Android sherpa-onnx Coexistence](docs/07-android-sherpa-onnx-coexistence.md)
- [Support Matrix and Migration](docs/13-support-matrix-and-migration.md)
- [Changelog](CHANGELOG.md)

Models, model metadata, and external data are treated as untrusted and are
bounded by `OrtResourceLimits`. Your app still owns model provenance, privacy
policy, provider SDK licensing, signing, and testing on its real targets.
