# Implementation Task Template

## Objective

Describe one vertical slice. Reference the requirement/ADR it implements.

## Scope

- In scope:
- Out of scope:
- Platforms/ABIs/flavors:
- Runtime ownership mode:
- Required ORT C API:
- sherpa-onnx impact:

## Design

- Dart API changes:
- C ABI changes:
- Ownership/lifetime model:
- Build/package changes:
- Provider/fallback behavior:
- Diagnostics:
- Security considerations:

## Tests required before completion

- Dart unit:
- Native unit/sanitizer:
- FFI integration:
- Packaged application:
- Artifact inspection:
- Provider assignment/parity:
- Android sherpa load orders:
- Failure injection/regression:

## Evidence to attach

- Commands and results.
- Diagnostic JSON.
- Native library inventory/import/export report.
- Model/runtime/provider versions and hashes.
- Benchmark data when performance behavior changes.
- Compatibility impact statement.

## Definition of done

- [ ] Code and generated bindings are committed.
- [ ] Documentation/support matrix is updated.
- [ ] Relevant acceptance checklist items pass.
- [ ] No unsupported fallback or packaging workaround remains.
