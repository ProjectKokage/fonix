# Instructions for Coding Agents

This file governs implementation work on the Dart ONNX Runtime wrapper. A coding agent must treat it as a repository-level contract.

## Objective

Implement an idiomatic, high-performance, diagnosable, and safely packaged ONNX Runtime wrapper for Dart and Flutter across iOS, macOS, Android, Linux, and Windows. The implementation must support multiple execution providers without weakening portability or allowing silent native-library conflicts.

## Required reading

Before modifying code, read `README.md` and every numbered document in `docs/`. When a task touches Android, read `docs/07-android-sherpa-onnx-coexistence.md` again before editing Gradle, CMake, build hooks, or native-loader code.

## Hard constraints

1. **Use a project-owned C shim.** Dart must call `dort_*` functions, not ONNX Runtime symbols directly.
2. **Keep the native ABI C-only.** Do not expose C++ classes, exceptions, `std::string`, `bool` with compiler-dependent layout, templates, or allocator-owned containers.
3. **Version every boundary.** Check shim ABI and ONNX Runtime C API before creating any ORT object.
4. **Do not package an unpinned runtime.** Every downloaded or built native input requires a fixed version, source reference, and SHA-256 digest.
5. **Do not download at application runtime.** Native binaries are resolved at build/package time or supplied explicitly by the host application on desktop.
6. **Do not hide fallback.** Provider registration, graph assignment, and fallback reasons must be inspectable. Strict mode fails if its provider requirement is unmet.
7. **Do not rely on finalizers for timely cleanup.** Every owning Dart object implements idempotent `dispose()`; finalizers only recover forgotten resources.
8. **Do not retain pointers into Dart-managed memory.** Copy by default. Zero-copy input/output requires a native allocation and a tracked owner.
9. **Do not pass native handles across isolates.** Async APIs must create and own sessions inside a dedicated worker isolate or copy data through a message-safe representation.
10. **Do not use `pickFirst` for ORT.** Duplicate `libonnxruntime.so` producers are a build failure, not a packaging preference.
11. **Do not ship two shared ORT binaries under different names as a workaround.** Renaming a SONAME does not establish semantic compatibility and creates two runtimes in one process.
12. **Do not claim an EP is active from session creation alone.** Capture ORT profiling/logging or another validated assignment signal.
13. **Do not broaden platform support without CI evidence.** A platform/ABI is supported only after packaging, runtime, and inference tests pass on that target.
14. **Do not hand-edit generated bindings.** Change `src/dort.h`, regenerate, and verify the diff.
15. **Preserve source and binary licensing.** Each artifact flavor must carry applicable ONNX Runtime and EP dependency notices.

## Mandatory implementation order

Do not begin with all EPs at once. Work in the following order and keep every stage green:

1. Repository scaffold, validated native lock, and no-op C shim loaded through Dart build hooks.
2. Runtime loader, shim ABI query, ORT version query, and C API negotiation.
3. CPU-only environment/session/run path with dense numeric tensors.
4. Deterministic ownership, error translation, metadata, strings, dynamic shapes, and resource tests.
5. Platform packaging for the five operating systems.
6. XNNPACK and CoreML mobile paths, with explicit provider diagnostics.
7. Android sherpa-owned process mode and final-artifact verification.
8. Desktop GPU/provider flavors.
9. Aligned Android custom-build mode and QNN qualification when the exact SDK,
   hardware, firmware, license, and redistribution inputs exist.
10. Remaining composite types, I/O binding, custom ops, reduced-operator builds, and release automation.

The current development sequence defers item 9 and all Windows target-host,
final-application, installer, and clean-machine qualification until their
required environments exist. Existing Android QNN contract/tamper checks and
Windows source, cross-build, and loader-security checks remain mandatory and
must not be promoted into runtime support claims.

## Change protocol

For each change:

- State which requirement or ADR it implements.
- Add or update tests before considering the task complete.
- Update the support matrix when behavior changes.
- Record native dependency changes in `native/versions.lock.yaml` and release notes.
- Include artifact-inspection evidence for packaging changes.
- Include benchmark and provider-assignment evidence for performance claims.
- Regenerate FFI bindings and fail CI if regeneration produces an uncommitted diff.
- Run format, static analysis, unit tests, native tests, and the relevant integration matrix.

A pull request that changes runtime loading, EP configuration, tensor ownership, or Android packaging must include a focused failure test that would detect the previous unsafe behavior.

## Required diagnostics

The public diagnostics API must expose at least:

- wrapper package version;
- shim ABI version and build identifier;
- requested and negotiated ORT C API versions;
- actual ONNX Runtime version string;
- runtime ownership/source mode;
- canonical runtime library identity where available;
- compiled, discoverable, registered, and active execution providers;
- provider options after normalization/redaction;
- session graph optimization and threading settings;
- fallback policy and recorded fallback reason;
- model identity/hash when supplied by the caller;
- platform, architecture, and native artifact flavor.

Do not include model contents, input tensors, paths containing private user data, or secrets in default logs.

## Android/sherpa-onnx gate

Before declaring Android compatibility:

1. Inspect the exact sherpa-onnx publication/source native inputs selected by
   the application, including raw federated-package `jniLibs` directories when
   that is the resolved Flutter layout.
2. Determine whether it contains shared `libonnxruntime.so` or statically embeds ORT.
3. Record the per-ABI ORT hashes and ELF metadata.
4. In `process` mode, prove that the wrapper artifact does not contain `libonnxruntime.so` and that its shim has no `DT_NEEDED` entry for it.
5. Build and statically audit the final APK and AAB, proving one ORT shared
   object per ABI. Runtime evidence must bind the exact installed APK; an AAB
   audit is separate until its delivered split is installed and exercised.
6. Validate separate target receipts after loading Fonix first and sherpa
   first on both 4 KiB and 16 KiB environments for every claimed ABI/build.
7. Check `GetVersionString()` and `GetApi(required_version)` at runtime.
8. Run the closed alternating Fonix inference and sherpa Silero VAD workload in
   the same process, including native cancellation settlement, between-frame
   VAD cancellation, stale-result suppression, recovery, both disposal orders,
   double disposal, and zero pending work.
9. Pass each raw schema-2 target receipt through the repository validator and
   give only its schema-1 validation record to the schema-2 compatibility
   manifest generator. Repeat the full matrix separately for release/R8 and
   debug builds.

If sherpa statically embeds ORT, the supported resolution is an aligned custom sherpa build that uses a shared external ORT. A dual-runtime exception requires an explicit ADR, separate library identity, memory/performance measurement, load-order testing, and clear unsupported-provider boundaries.

## Coding standards

### Dart

- Enable sound null safety and the repository's strict analyzer options.
- Keep public APIs small; use sealed classes/enums for finite policies and immutable options objects.
- Validate dimensions, byte counts, names, and type compatibility before crossing FFI.
- Use `int` only after range checking against native `size_t`, `int64_t`, and enum domains.
- Preserve the original native error code and message in typed Dart exceptions.
- Avoid hidden global initialization in import-time code.
- Make disposal idempotent and reject method calls after disposal.
- Keep worker-isolate protocol versioned and serializable.

### C

- Compile as C11 or a conservative C99 subset where practical.
- Build with warnings-as-errors in CI.
- Check every allocation and multiplication for overflow.
- Initialize all out parameters on failure.
- Never call an ORT function through a null function-table pointer.
- Copy ORT error messages before releasing `OrtStatus`.
- Export only `dort_*` symbols and the required platform entry points.
- Use struct-size fields for extensible input/output structures.
- Native release functions accept null, but each retained non-null reference is released exactly once; Dart provides idempotent disposal by clearing its pointer before release.
- Use explicit-width integer types at the ABI boundary.
- Keep runtime-loader handles alive for the process lifetime unless a platform-specific proof establishes safe unload.

### Native build

- Default to hidden symbol visibility.
- Use release optimization with symbols retained in separate debug artifacts.
- Enable dead-code stripping and section GC where compatible.
- Keep provider shared libraries adjacent to the matching ORT shared library.
- Never place ORT provider libraries in a global system search path.
- Use secure, application-local DLL loading on Windows.
- Validate Android ELF alignment and 16 KB page-size support.

## Completion report format

At the end of an implementation task, report:

```text
Implemented:
- ...

Tests and evidence:
- command / target / result
- final artifact inspection
- provider assignment evidence, when relevant

Compatibility impact:
- shim ABI:
- ORT API/runtime:
- platforms/ABIs:
- sherpa-onnx mode:

Remaining risks:
- ...
```

Do not mark work complete when only code generation or compilation succeeds. The relevant packaged application must load and run.
