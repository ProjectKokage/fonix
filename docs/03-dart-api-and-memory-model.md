# 3. Dart API and Memory Model

This document describes the implemented public API and ownership semantics for
the current development release.

## 3.1 Package surface

Keep the primary library small:

```dart
import 'dart:typed_data';

import 'package:fonix/fonix.dart';
```

Recommended public concepts:

- `OrtRuntime`
- `OrtRuntimeSource`
- `OrtRuntimeInfo`
- `OrtSession`
- `OrtSessionOptions`
- `OrtRunOptions`
- `OrtExecutionProvider`
- `OrtFallbackPolicy` and `OrtProviderRequirement`
- `OrtValue`
- `OrtTensor`
- `OrtNativeBuffer`
- `OrtTypeInfo`
- `OrtModelMetadata`
- `OrtIsolateSession`
- typed `OrtException` subclasses

Provider-specific convenience APIs may live in sublibraries so applications do not import irrelevant options.

## 3.2 Runtime API sketch

```dart
final runtime = OrtRuntime.open(
  source: const OrtRuntimeSource.bundled(),
  requiredApi: OrtApiVersion.v27,
  logSeverity: OrtLogSeverity.warning,
);

try {
  print(runtime.info.runtimeVersion);
  print(runtime.providerDiscovery.toJson());
} finally {
  runtime.dispose();
}
```

Android sherpa-owned mode:

```dart
final runtime = OrtRuntime.open(
  source: OrtRuntimeSource.process(
    preferredLibraryNames: ['libonnxruntime.so'],
  ),
  requiredApi: OrtApiVersion.v27,
);
```

Desktop managed-runtime mode must require an absolute path by default:

```dart
final runtime = OrtRuntime.open(
  source: OrtRuntimeSource.file(
    absolutePath: runtimePath,
    allowedRoot: installationRoot,
  ),
);
```

The API should reject unsupported source modes at build/runtime with a precise message.

## 3.3 Session API sketch

```dart
final options = OrtSessionOptions(
  graphOptimization: OrtGraphOptimization.all,
  executionMode: OrtExecutionMode.sequential,
  intraOpThreads: 1,
  interOpThreads: 1,
  providers: [
    OrtExecutionProvider.xnnpack(
      intraOpThreads: 4,
      requirement: OrtProviderRequirement.preferred,
    ),
    OrtExecutionProvider.cpu(),
  ],
  fallbackPolicy: OrtFallbackPolicy.report,
  artifactRoot: providerEvidenceRoot,
);

final session = OrtSession.fromFile(
  runtime: runtime,
  modelPath: modelPath,
  options: options,
);

try {
  final result = session.run(
    inputs: {'input': inputTensor},
    outputNames: const ['output'],
  );
  try {
    final output = result.tensor('output');
    final evidence = result.providerEvidence;
    final providerStates = result.providerDiagnostics;
    final Float32List data = output.copyFloat32Data();
    // Consume data.
  } finally {
    result.dispose();
  }
} finally {
  session.dispose();
}
```

Tensor element type is checked by typed copy/view methods rather than an invalid Dart `float` type argument. Input creation should likewise use concrete typed-data constructors:

```dart
final input = OrtTensor.fromFloat32List(
  runtime: runtime,
  values: values,
  shape: const [1, 3, 224, 224],
);
```

Avoid a generic API that accepts any `List<num>` and performs ambiguous narrowing.

## 3.4 Session options

`OrtSessionOptions` copies and validates all constructor inputs into immutable
state. Implemented fields include:

- graph optimization level;
- execution mode;
- intra-op/inter-op threads;
- CPU memory arena enablement;
- memory pattern enablement;
- profiling configuration;
- optimized model output path with explicit overwrite policy;
- deterministic compute when supported;
- log severity/verbosity and session log ID;
- ordered provider requests;
- fallback/assignment requirement;
- config entries as an advanced escape hatch.

Provider convenience constructors must apply required cross-options. For example, DirectML selection should force or validate sequential execution and disabled memory patterns rather than allow a known-invalid combination.

Do not mutate caller-provided maps. Normalize options into immutable ordered data before FFI.

`artifactRoot` is accepted exactly when the session configures explicit
profiling, optimized-model output, a CoreML cache, `requireActive`/
`requireFullAssignment`, `rejectAny`, or `report`/`rejectCpu` with a non-CPU
provider. Fonix
enables ORT 1.25+ profiling on the run options, validates and removes the one
bounded native profile, enforces policy, and only then constructs the public
run result. `OrtRunResult.providerEvidence` and
`OrtRunResult.providerDiagnostics` are immutable receipts for that exact run;
session-level diagnostics never infer node assignment from successful session
creation. Worker-isolate results carry the same copied receipts. Worker
protocol version 4 installs the bounded startup timer before beginning
`Isolate.spawn`, then spawns the worker paused so the controller can establish
ownership before native setup. After resume, the worker publishes one
authoritative command port before fallible native setup, then requires the
ready message to repeat that exact port. This lets a timed-out or malformed
startup queue one graceful close without force-killing an isolate while native
code may own resources; the worker consumes that close if setup returns. If
an ownership reply is malformed before its command port can be trusted, a
later syntactically valid ready port is accepted only as a cleanup channel: it
receives `close`, can never publish a session, and is retired after the
validated `closed` receipt. If
the deadline wins before `Isolate.spawn` returns, the controller closes its
bootstrap ports and kills the late isolate while it is still paused, so an
abandoned caller cannot start native initialization. A validated `closed`
receipt is emitted only after native owners are disposed;
the controller then sends `retire`, so worker exit can never race ahead of the
required cleanup receipt. Worker-authored startup errors and fatal
protocol/cleanup replies also wait for a controller `retire` acknowledgement
before exiting; the controller retains its bootstrap resources until the
observed exit proves cleanup. Uncaught-isolate error and exit notifications use
one ordered lifecycle port, so the exact bounded error is authoritative over
the following exit instead of being replaced by a generic crash. The protocol also
preserves typed CoreML cache configuration and copies the full redacted
session/run diagnostics snapshot; it never sends the cache path or another
private path back to the caller.

## 3.5 Type system

ONNX element types are represented explicitly:

```dart
enum OrtTensorElementType {
  undefined,
  float32,
  uint8,
  int8,
  uint16,
  int16,
  int32,
  int64,
  string,
  boolean,
  float16,
  float64,
  uint32,
  uint64,
  complex64,
  complex128,
  bfloat16,
  float8E4M3FN,
  float8E4M3FNUZ,
  float8E5M2,
  float8E5M2FNUZ,
  uint4,
  int4,
  float4E2M1,
  uint2,
  int2,
  float8E8M0,
}
```

The enum must match the selected ORT API's actual supported values and be generated or verified against headers. An enum entry may be introspectable before the wrapper offers a convenient Dart storage type. Unsupported creation/conversion must fail explicitly.

### Storage mapping

- `Float32List`, `Float64List` for corresponding floating types.
- `Int8List`, `Uint8List`, `Int16List`, `Uint16List`, `Int32List`, `Uint32List`, `Int64List`, `Uint64List` for integer types.
- `Uint8List` or a dedicated bit/boolean adapter for ONNX bool, with normalization to 0/1.
- `Uint16List` wrappers for float16 and bfloat16 raw storage, plus explicit conversion utilities.
- Dedicated packed storage abstractions for 4-bit/sub-byte types.
- Dedicated complex value abstraction or interleaved typed storage with explicit convention.
- `List<String>` for copy-based string tensors.

Never infer ONNX element type solely from a Dart `List` whose runtime element type loses width/signedness.

## 3.6 Shapes

- Tensor dimensions cross native boundaries as signed 64-bit integers.
- Concrete tensor creation rejects negative dimensions.
- Metadata may expose `null`/symbol names for dynamic dimensions.
- Rank-zero scalar shape is `const []` and contains exactly one element.
- A zero-length dimension is valid when ORT/model semantics permit it; element count becomes zero.
- Element-count multiplication uses checked arithmetic and rejects overflow before allocating.
- Byte length must equal element count times storage width for fixed-width types.
- Symbolic dimension names are metadata, not values supplied to a concrete tensor.

`OrtShape` implements these checks and exposes immutable dimensions plus the
checked element count.

## 3.7 Copy-based input tensors

Default constructors copy into wrapper-owned native storage:

```dart
final tensor = OrtTensor.fromFloat32List(
  runtime: runtime,
  values: values,
  shape: [1, values.length],
);
```

Properties:

- caller may mutate/release the Dart list after construction;
- tensor owns native storage and the `OrtValue` relationship;
- creation validates byte count and shape;
- disposal is deterministic;
- finalizer releases forgotten native state;
- repeated inference can reuse the same tensor if the session/run semantics do not mutate it.

A convenience constructor may defer the native copy until run only if it cannot retain the Dart pointer and the resulting lifetime remains obvious. Simplicity favors copying at tensor creation.

## 3.8 Native buffers and zero-copy

Expose an advanced allocation:

```dart
final buffer = OrtNativeBuffer.allocate(
  runtime: runtime,
  byteLength: elementCount * Float32List.bytesPerElement,
);
try {
  final bytes = buffer.viewBytes();
  final data = bytes.buffer.asFloat32List(bytes.offsetInBytes, elementCount);
  data.setAll(0, values);

  final tensor = OrtTensor.fromNativeBuffer(
    buffer: buffer,
    shape: [1, elementCount],
    elementType: OrtTensorElementType.float32,
  );
  try {
    // Run with the tensor. It retains the buffer until disposed.
  } finally {
    tensor.dispose();
  }
} finally {
  buffer.dispose(); // Actual free is deferred while retained.
}
```

Requirements:

- native allocation alignment is adequate for ORT and selected providers;
- C-side reference counting protects retained buffers;
- Dart typed-data views are attached to a native finalizer/ref token or are scoped through a callback;
- disposing a high-level owner marks it unusable but does not free memory still referenced by a view/tensor;
- native buffers cannot be sent to another isolate;
- an unsafe pointer constructor, if provided, lives in an explicitly unsafe sublibrary and requires a custom deleter/lifetime token.

Do not advertise zero-copy when ORT/provider later copies to device memory. Call the path “host zero-copy” and report device transfers separately.

## 3.9 Outputs

Safe default:

```dart
final Float32List data = output.copyFloat32Data();
```

Advanced native-backed access may use:

```dart
final Float32List view = output.viewFloat32Data();
// The read-only view owns a native data lease and remains valid if the
// output/result is disposed. Its lease is released by its finalizer.
```

The API must make ownership impossible to misunderstand. Returning a raw `TypedData` view whose allocation may be freed when the `OrtValue` object is disposed is prohibited.

`OrtRunResult` owns output values. Disposing the result disposes values that have not been independently retained. A requested retained output increments the native wrapper reference count.

## 3.10 Strings and composite values

String tensors use explicit UTF-8 conversion and copy semantics. Account for:

- empty strings;
- non-ASCII text;
- embedded NUL policy (reject if ORT API cannot represent it safely);
- total byte-size overflow;
- output string-content extraction and offset calculation;
- release on partial failure.

Both direct and isolate string tensors enforce the native closed limits before
publication: at most 1,048,576 elements, at most 1 MiB of UTF-8 per element,
and at most 64 MiB of UTF-8 content. Worker message and aggregate-input budgets
also charge a fixed 8-byte retained-slot cost for every string, so an
arbitrarily large list of empty strings cannot bypass byte backpressure.

Sequences, maps, and optionals are closed `OrtValue` implementations. They
preserve recursive element types and validate/copy ONNX-ML map keys and values
before exposing application state.

## 3.11 Metadata

Expose immutable metadata:

```dart
final List<OrtValueInfo> inputs = session.inputs;
final List<OrtValueInfo> outputs = session.outputs;
final OrtModelMetadata metadata = session.modelMetadata;
```

Include:

- names;
- value kind;
- tensor element type;
- dimensions and symbolic dimension names;
- sequence/map/optional nested types;
- model producer, graph name, domain, description, version, and custom metadata map.

Copy native strings into Dart and release native allocators promptly. Metadata objects should remain usable after session disposal unless documented otherwise, so prefer copied Dart values.

## 3.12 Errors

Public calls throw typed exceptions. Every exception should include:

- operation;
- wrapper code;
- optional ORT error code;
- native message;
- runtime/provider context safe for logs;
- nested cause where a local validation or isolate error wraps another failure.

Messages are diagnostic, not machine APIs. Applications should branch on exception type/code.

Local errors such as use-after-dispose, invalid dimensions, duplicate input names, or unsupported platform mode should be detected before FFI.

## 3.13 Disposal

Synchronous native owners implement a common interface:

```dart
abstract interface class Disposable {
  bool get isDisposed;
  void dispose();
}
```

Rules:

- `dispose()` is idempotent.
- Methods called after disposal throw immediately.
- A finalizer is detached on successful explicit disposal.
- Parent objects use native ref counts or child tracking; never free a runtime/environment while a child can call it.
- Finalizer callbacks call only a small native release function and never re-enter Dart.
- Finalizer order is not assumed.
- Large native buffers attach accurate external memory pressure where Dart APIs support it.

Tests must exercise every disposal permutation, including parent-before-child, view-before-value, repeated disposal, forgotten disposal, and isolate termination.

`OrtIsolateSession` and `OrtSessionPool` instead own asynchronous worker
resources and expose idempotent `close()` methods that wait for native work to
settle before isolate termination.

## 3.14 Async API

Do not simply wrap a blocking FFI call in `Future.sync`; that still blocks the isolate. Provide a worker abstraction:

```dart
final worker = await OrtIsolateSession.spawn(
  runtimeSource: OrtRuntimeSource.file(
    absolutePath: runtimePath,
    allowedRoot: installationRoot,
  ),
  model: OrtModelSource.file(
    absolutePath: modelPath,
    allowedRoot: modelRoot,
  ),
  options: options,
);
try {
  final outputs = await worker.run(inputs: inputs);
} finally {
  await worker.close();
}
```

The worker:

- loads runtime and creates session inside the worker isolate;
- uses a versioned message protocol;
- copies or uses `TransferableTypedData` for host data;
- serializes runs by default;
- supports cancellation as cooperative ORT run termination where possible;
- reports worker crashes separately from ORT errors;
- fails active public work immediately on a terminal protocol error while
  retaining controller ownership until native cleanup settles; and
- requires a cleanup receipt before normal isolate retirement, with uncaught
  errors and exit delivered through one ordered lifecycle channel.

The shipped protocol accepts only exact, versioned message fields and closed
`OrtIsolateValue` variants. Numeric payloads are copied into
`TransferableTypedData`; strings and recursive sequence/map/optional values are
validated against depth, node, shape, element, and byte limits. In particular,
string tensors retain the direct API's element/content limits and consume a
fixed per-element budget even when every string is empty. Optional None carries
its recursive element type. No pointer, native wrapper, runtime, session, or
run-options handle crosses an isolate message.

`startRun` returns an `OrtIsolateRun` whose idempotent `cancel` operation either
removes a queued request or uses an opaque process-local token to request ORT
termination. `cancelWithDisposition` exposes the exact closed outcome:
`queuedRunRemoved`, `nativeTerminationRequested`, or `notCancelled`. The native
outcome means the active registry accepted the request; authoritative run
settlement still comes from awaiting `result`. Runs remain serialized per
worker. `maxPendingRuns` bounds request count, while
`maxOutstandingInputBytes` bounds the measured bytes reserved by all active and
queued inputs; it defaults to `maxMessageBytes` and is independently capped at
1 GiB. The per-message bound includes input names and values, every retained
string slot, and requested output names; output-name bytes are not charged to
the aggregate input reservation. A request larger than that aggregate limit
can never fit and raises
`OrtWorkerMessageTooLargeException`; temporary aggregate exhaustion raises
`OrtWorkerQueueFullException` with the bound, current reservation, and request
size. Dispatch drops the controller's input references, but its reservation is
retained until the worker disposes every ordinary per-run native owner before
publishing authoritative settlement. A malformed or fatal reply retains the
active reservation until the worker's cleanup receipt or observed exit. Queued
cancellation releases both immediately. Graceful close rejects queued work,
requests cancellation of the active run, waits for its native call to return,
and disposes session then runtime. It intentionally has no timeout that silently
kills a provider while native state may still be live. A malformed run reply
follows the same ordered retirement path: the public run fails immediately, but
`close()` settles only after the worker reports cleanup or exits. Exit without
the required receipt is a worker crash, not a successful close.

An explicitly sized `OrtSessionPool` may own multiple workers for throughput.
It selects the least-loaded live worker with round-robin tie breaking, skips a
worker without byte capacity for the measured request, preserves terminal
worker failures ahead of queue-full errors, and cleans up a partially started
pool. `maxOutstandingInputBytesPerWorker` configures the same per-worker bound.
Pool size is a measured configuration, not CPU-count magic.

## 3.15 Provider API

Use typed convenience constructors over a bounded generic representation:

```dart
final providers = <OrtExecutionProvider>[
  OrtExecutionProvider.tensorRt(enableFp16: true),
  OrtExecutionProvider.cuda(
    deviceId: 0,
    arenaExtendStrategy: OrtMemoryArenaExtendStrategy.nextPowerOfTwo,
  ),
  OrtExecutionProvider.cpu(),
];

final directMl = OrtExecutionProvider.directMl(deviceId: 0);
final openVino = OrtExecutionProvider.openVino(
  deviceMode: OrtOpenVinoDeviceMode.auto,
  devices: const [OrtOpenVinoDevice.gpu, OrtOpenVinoDevice.cpu],
);
final oneDnn = OrtExecutionProvider.oneDnn(useArena: true);
final miGraphX = OrtExecutionProvider.miGraphX(enableFp16: true);
```

Typed constructors:

- validate enum/range/path values;
- normalize booleans and numbers to the exact strings expected by the selected ORT version;
- redact sensitive paths/options in logs where needed;
- carry compatibility metadata;
- do not silently drop unknown options.

Generic named providers are a bounded escape hatch. Known provider-specific
IDs still apply their closed key/value schemas, so `named` cannot bypass the
typed CUDA, TensorRT, DirectML, OpenVINO, oneDNN, or MIGraphX safety checks.

## 3.16 Diagnostics API

Example:

```dart
final runtimeReport = runtime.diagnostics;
final sessionReport = session.diagnostics;
final resultReport = result.diagnostics;
print(resultReport.toJson());
```

A report should be deterministic and machine-readable. Keep `compiled`,
`discoverable`, `registered`, `active`, and `qualified` as separate nullable
facts: none proves another. `OrtRuntime.diagnostics` contains build, negotiated
runtime, source/owner, target, artifact, and discovery facts;
`OrtSession.diagnostics` adds immutable session policy and registration;
`OrtRunResult.diagnostics` adds the exact run's active-provider evidence. The
same snapshots are available from `OrtIsolateSession` and
`OrtIsolateRunResult`. Keep verbose ORT logs opt-in and never include tensor
contents or artifact-root/cache paths by default. External runtime artifacts
have a null artifact digest and unknown compiled state rather than a fabricated
identity. For exact bundled or linked artifacts, `compiled` comes only from the
provider inventory embedded from the validated native lock; discovery is never
substituted for that build fact.

## 3.17 API stability

- Public Dart API follows semantic versioning.
- Shim ABI has an independent integer version.
- ORT runtime/API compatibility is reported separately.
- Experimental APIs live under an explicit experimental library/annotation.
- Provider option keys are versioned data, not stable Dart enum values unless the wrapper controls normalization for all supported ORT versions.

The canonical review record for the resolved `package:fonix/fonix.dart`
namespace is
[`../release/public-dart-api-v1.json`](../release/public-dart-api-v1.json).
It records each exported declaration's defining library, resolved nominal
types throughout the declaration graph, signatures, modifiers, constructor
redirect chains, extension-type erasure, properties, methods, evaluated public
constants and optional defaults, ordered enum constants, and resolved
library/declaration/parameter annotations with their canonical constant
values. Type aliases retain both their declared identity and expanded target.
The generator uses the exact minimum Dart 3.11.5 toolchain and analyzer
14.1.0. Verify it after a locked dependency resolution with:

```bash
dart pub get --enforce-lockfile --no-example
dart --packages=.dart_tool/package_config.json \
  tool/ci/verify_public_dart_api.dart
```

Regenerate the record only as part of an intentional API review. Before it
constructs the record, the gate resolves every regular Dart source below
`lib/` within fixed entry bounds. Links, analysis or constant-evaluation
errors, conditional imports/exports, environment-dependent constants, private
`Symbol` values, and function-type formal/type-parameter metadata that the
pinned analyzer cannot represent all fail closed. Instantiated generic
function tear-off constants also fail closed because analyzer 14.1 does not
expose their type arguments canonically. Constructor tear-offs retain their
complete redirect chain, but fail closed when any hop has an explicit
parameter default that analyzer 14.1 cannot expose canonically. Canonical
constant encoding retains observable collection order, double bit patterns,
enum ordinals, and both non-erased and erased extension-type identities. A
clean comparison means the reviewed declaration surface did not drift; it does
not decide
whether a change is source/binary compatible, promise pre-1.0 stability,
prove runtime behavior, or replace external API/ABI approval.
