# Working in Fonix

Fonix is a Dart/Flutter ONNX Runtime wrapper with a project-owned C shim and
explicit native runtime ownership.

## Start here

Read [README](README.md), then the documents for the boundary being changed:

| Change | Read |
| --- | --- |
| Ownership, async execution, Dart API | [Architecture](docs/02-architecture.md), [memory model](docs/03-dart-api-and-memory-model.md) |
| Native shim or bindings | [C ABI](docs/04-native-c-abi.md) |
| Build hooks, loaders or artifacts | [Packaging](docs/05-build-and-packaging.md) |
| Execution providers | [Provider contracts](docs/06-execution-providers.md) |
| Android, Gradle, CMake or sherpa coexistence | [Android coexistence](docs/07-android-sherpa-onnx-coexistence.md) |
| Test or evidence requirements | [Verification](docs/08-testing-benchmarking-and-ci.md), [executed results](VALIDATION.md) |
| Scope, support or release | [Requirements](docs/01-scope-and-requirements.md), [decisions](docs/11-decisions-and-open-questions.md), [support matrix](docs/13-support-matrix-and-migration.md), [release](docs/09-security-release-and-maintenance.md) |

Read relevant sections rather than every numbered document. The
[implementation plan](docs/10-implementation-plan.md) owns sequencing and
explicitly deferred target qualification; a source check cannot close those gates.
Inspect the working tree, preserve unrelated work, and state scope and checks.

## Essential contracts

- Dart calls the versioned `dort_*` C shim. Check shim ABI and ORT C API before
  creating ORT objects. Never expose C++ ABI or call through an invalid table.
- Validate dimensions, lengths, integer ranges and allocation arithmetic before
  FFI. Initialize failure outputs and preserve native errors in typed exceptions.
- Copy Dart-managed data by default. Native views require a tracked owner;
  handles must not cross isolates. Async sessions belong to their worker isolate.
- Dispose deterministically and idempotently, including after partial setup.
  Finalizers only recover forgotten resources. Preserve cancellation settlement,
  stale-result suppression and loader lifetime; do not unload ORT without proof.
- Pin native inputs by version, source and checksum in
  [native/versions.lock.yaml](native/versions.lock.yaml). No application-runtime
  native downloads. Preserve notices and secure application-local loading.
- Android has one shared ORT owner. Do not use `pickFirst` or renamed duplicate
  runtimes. A dual-runtime exception needs the explicit ADR and load-order,
  identity, memory and provider evidence required by the coexistence contract.
- Provider registration is not proof of graph assignment. Keep fallback and
  provider diagnostics inspectable; strict mode fails unmet requirements.
  Keep model contents, tensors, private paths, tokens and secrets out of logs.
- Regenerate bindings from `src/dort.h` when the ABI/header or binding inputs
  change. Never edit generated bindings by hand or weaken checks to pass.

## Verification

For Dart code changes, run targeted tests, then from the repository root:

```sh
dart format --output=none --set-exit-if-changed .
dart analyze --fatal-infos
dart test
```

For verifier/CI tooling changes, run the relevant Python suite:

```sh
python3 -B -m unittest discover -s tool/ci/tests -p 'test_*.py'
python3 -B -m unittest discover -s tool/tests -p 'test_*.py'
```

Use the [verification guide](docs/08-testing-benchmarking-and-ci.md) and
[CI](.github/workflows/ci.yml) for affected native, binding and runtime checks.
Loading, provider, tensor-ownership and Android-packaging fixes need a focused
failure regression. Packaging/support claims require exact final-app execution
and artifact inspection; performance/provider claims require their measurements.
Keep unavailable target qualification open.

For prose-only changes, check links, referenced commands and `git diff --check`.
Do not regenerate bindings or build application packages solely for prose edits.
Report executed checks, ABI/runtime/platform impact and remaining evidence gaps.

## Delivery

Use a task branch and review the complete diff. Make the smallest complete fix;
obtain approval for material scope or recorded product/release decision changes.
Commit only task files; push or publish only when requested, and merge only with
owner approval. Update the owning contracts/support matrix when facts change;
put routine execution output with the result rather than duplicating it in policy.
