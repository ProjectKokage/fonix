# Fonix documentation

The [package README](../README.md) is the place to start: it covers setup,
running a model, worker isolates, and execution providers. The documents below
are the detailed contracts behind it.

## Using Fonix in an app

| Document | Read it when you need |
| --- | --- |
| [3. Dart API and Memory Model](03-dart-api-and-memory-model.md) | Every value type, metadata, ownership and disposal rules, native buffers, worker-isolate behavior, and error types |
| [5. Build and Packaging](05-build-and-packaging.md) | Build-hook settings, the artifact cache, per-platform packaging, and the Flutter asset step |
| [6. Execution Providers](06-execution-providers.md) | Options and limits for each provider, and how assignment is verified |
| [7. Android sherpa-onnx Coexistence](07-android-sherpa-onnx-coexistence.md) | Shipping Fonix and sherpa-onnx in one Android app with a single ONNX Runtime |
| [13. Support Matrix and Migration](13-support-matrix-and-migration.md) | What has actually been tested on each platform and provider, and how to migrate from earlier previews |

## Design and contracts

| Document | Contents |
| --- | --- |
| [1. Scope and Requirements](01-scope-and-requirements.md) | Product goals, platform tiers, functional requirements, non-goals, and milestones |
| [2. Architecture](02-architecture.md) | Layers, runtime ownership modes, version negotiation, tensor ownership, isolates, and errors |
| [4. Native C ABI](04-native-c-abi.md) | The `dort_*` C shim interface, status convention, loaders, and hardening |
| [9. Security, Release, and Maintenance](09-security-release-and-maintenance.md) | Threat model, resource limits, library loading, privacy, versioning, and the release checklist |
| [11. Decisions and Open Questions](11-decisions-and-open-questions.md) | Accepted architecture decisions (ADR-001 to ADR-015) and unresolved questions |
| [12. Reference Snapshot](12-reference-snapshot.md) | Upstream ONNX Runtime, provider, Flutter, and sherpa-onnx facts as of 2026-08-07 |

## Working on Fonix

| Document | Contents |
| --- | --- |
| [8. Testing, Benchmarking, and CI](08-testing-benchmarking-and-ci.md) | Test layers, packaging and coexistence tests, provider qualification, and CI lanes |
| [10. Implementation Plan](10-implementation-plan.md) | Development order and the acceptance criteria for each phase |
| [`AGENTS.md`](../AGENTS.md) | Hard constraints and the change protocol for contributors and coding agents |
| [`VALIDATION.md`](../VALIDATION.md) | The latest local check run and what it did not cover |
| [Reference app](../example/README.md) | The Flutter reference app, its smoke and benchmark modes, and platform gates |

## Dated evidence

- [2026-09-28 iOS 27 consumer qualification](evidence/2026-09-28-ios27-consumer.md)
