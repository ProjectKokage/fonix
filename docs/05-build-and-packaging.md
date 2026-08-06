# 5. Build and Packaging

## 5.1 Build-system strategy

Use Dart build hooks as the primary mechanism for compiling and bundling the project-owned C shim. Build hooks became stable in Dart 3.10. A link hook may be added when the project raises its SDK floor to a Dart release that supports it and when native tree-shaking is proven not to remove dynamically resolved entry points.

The build hook must be deterministic from:

- target operating system and architecture;
- selected runtime ownership mode;
- selected native flavor/EP set;
- deployment target;
- lockfile content;
- explicit user-defined build configuration.

It must not query a moving release endpoint or select “latest.”

## 5.2 Package split

Recommended publication model:

- `fonix`: Dart API, shim sources, generated bindings, diagnostics, and the
  lock-selected CPU baseline.
- Optional provider/binary packages only where build-hook dependency composition is reliable and the publication burden is justified.
- Application-owned aligned-build tooling for sherpa/QNN and other custom combinations.

Avoid publishing one enormous artifact containing CUDA, TensorRT, OpenVINO, QNN, DirectML, CoreML, XNNPACK, and every transitive dependency. Heavy EPs require different SDKs, licenses, drivers, architectures, and update cadences.

A practical future flavor taxonomy follows. The current schema-2 lock selects
only exact `cpu` artifacts; `external` is a shim/runtime ownership profile, not
a native artifact flavor.

| Flavor | Intended targets | Contents |
|---|---|---|
| `cpu` | all | ORT CPU/MLAS + shim |
| `mobile` | iOS/Android | CPU + XNNPACK + platform EPs supported by official/mobile build |
| `apple` | iOS/macOS | CPU + XNNPACK + CoreML |
| `cuda` | Windows/Linux | CPU + CUDA provider/dependencies expected from selected distribution |
| `tensorrt` | Windows/Linux | CPU + CUDA + TensorRT provider stack |
| `directml` | Windows | CPU + DirectML |
| `openvino` | Windows/Linux | CPU + OpenVINO provider stack |
| `migraphx` | qualified Linux/AMD GPU | CPU + pinned MIGraphX/ROCm dependency stack |
| `webgpu-native` | qualified Windows/Linux/macOS | CPU + native WebGPU EP + pinned Dawn/backend dependencies |
| `nv-tensorrt-rtx-plugin` | qualified NVIDIA RTX desktop targets | standard compatible ORT + trusted standalone TensorRT RTX EP plugin |
| `vitisai` | qualified Windows/Linux vendor targets | CPU + exact Vitis AI runtime/compiler dependency set |
| `qnn-android-aligned` | Android | one source-built ORT with integrated QNN, shared by the wrapper and sherpa-onnx |
| `qnn-windows` | Windows ARM64 | qualified built-in or plugin QNN distribution and exact SDK/runtime dependencies |
| `plugin-*` | qualified desktop targets | ORT plus one pinned, trusted plugin EP and its companion libraries |

Published names can differ, but every implemented artifact flavor must appear
in diagnostics and the lock manifest.

## 5.3 Native version lockfile

[`../native/versions.lock.yaml`](../native/versions.lock.yaml) is the exact
schema-2 source of truth. It records the snapshot/release state, shim ABI and
source revision, compatibility-floor headers and licenses, the closed release
target matrix, and every exact artifact. Do not duplicate an abridged lock
template: it will either be invalid or drift from the authoritative schema and
artifact inventory.

The actual project lockfile must also include, per artifact:

- canonical URL or source repository/tag/commit;
- SHA-256 digest;
- archive format and expected files;
- license identifiers/notice paths;
- target OS/architecture/deployment target;
- ORT build flags and reduced-operator configuration;
- stable wrapper provider IDs and the provider names expected from ORT discovery/logging, kept as separate fields;
- selected EPs and their dependent-library versions;
- compiler, toolchain, CMake, NDK/Xcode/MSVC versions where reproducibility requires them;
- patches and their digests;
- whether the shim is linked, bundled-dynamic, or runtime-resolved.

The committed lock contains exact source, payload, and notice digests for every
baseline tuple. Its current release blockers are the explicit
`unreleased-preview` state and null shim source revision, not missing artifact
hashes. Release validation refuses either blocker, an empty digest, or a
placeholder value.

## 5.4 Build-hook behavior

The hook should:

1. Parse target OS/architecture and project configuration.
2. Read and validate the lockfile schema.
3. Select exactly one supported artifact/flavor tuple.
4. Compile the shim with the matching loader mode.
5. Resolve artifacts only from an explicitly provisioned cache or mirror,
   verify digest before extraction, and reject path traversal; the hook does
   not perform an implicit network download.
6. Fail closed when the selected locked bytes are unavailable. Explicit source
   builds use their owning reproducible tool and must produce a reviewed lock
   entry before the normal hook can consume them.
7. Emit code assets with consistent names across architecture slices.
8. Emit/retain a native build manifest for diagnostics.
9. Avoid mutating the source checkout.
10. Fail with actionable diagnostics when the target/flavor is unsupported.

Tests must invoke the hook through normal Dart/Flutter build and test commands, not only by calling helper functions.

### Flutter application contract for Apple floors and notices

Flutter's native-assets protocol currently forwards a tool-owned integer Apple
`targetVersion`; it is not authoritative evidence of the consuming
application's deployment target. A Fonix iOS linked build or macOS bundled
build therefore requires the application to declare its floor explicitly:

```yaml
hooks:
  user_defines:
    fonix:
      runtime_mode: bundled # omit on iOS, whose default is linked
      artifact_cache: /absolute/offline/cache
      application_minimum_os: '14.0' # use '15.1' for the locked iOS tuples
```

The hook parses `application_minimum_os` as strict
`major.minor[.patch]`, rejects a missing or lower declaration, and compiles the
shim at the exact minimum recorded by the selected lock tuple. The application
project's Xcode deployment target remains independently authoritative and must
be at least the declaration. The final application audit checks both its
`Info.plist` and the load commands of the app executable, Fonix shim, and
packaged ORT binary.

Code assets cannot carry the generated staging manifest and exact upstream
notice as ordinary Flutter data on the current supported Flutter toolchain.
Before Flutter collects assets, the application must prepare its app-owned
copies with the same resolver used by the hook:

```bash
dart run fonix:fonix_prepare_flutter_assets \
  --target-os macos \
  --architecture arm64 \
  --variant default \
  --cache /absolute/offline/cache \
  --output /absolute/application/assets/fonix
```

The application declares both exact output files:

```yaml
flutter:
  assets:
    - assets/fonix/fonix-native-artifact-manifest.json
    - assets/fonix/ThirdPartyNotices.txt
```

The resolver publishes only lock-verified bytes. The shim independently embeds
a closed identity containing the artifact ID, lock/source hashes, target,
flavor, runtime mode, minimum OS, and exact `ThirdPartyNotices.txt` hash. The
final audit fails if the app omits, duplicates, substitutes, or misplaces either
Flutter asset, or if the embedded identity and staged manifest differ.
Every explicit `--package-root`, `--cache`, `--mirror`, and `--output` path must
be absolute. If `--package-root` is omitted, the executable resolves the
installed `package:fonix` location through the active package configuration.
Each file is promoted through its own temporary file; the two-file publication
is not one filesystem transaction. Consequently, the final application audit
remains mandatory and rejects a mixed or partially updated pair.

### Offline and mirrored builds

Support an explicit artifact cache/mirror configuration. A cache hit is accepted only after digest verification. Do not silently fall back from a configured offline mode to the network.

### Credentials

Build hooks must not embed tokens in generated files, logs, command lines, or release metadata. Proprietary SDK artifacts such as QNN may require application-owned provisioning outside public package publication.

## 5.5 Building the shim

Use a small CMake project or `native_toolchain_c` integration with equivalent controls:

- C11/C99-compatible source;
- hidden visibility;
- warnings as errors for project code;
- platform export map/list;
- linked/runtime-resolved loader definition;
- embedded build manifest (version, ABI, flavor, exact lock-declared compiled
  provider inventory, and git revision if reproducible);
- no link to ORT in external/process mode;
- exact ORT headers at the compatibility floor for the base shim;
- provider-specific compile definitions only when needed.

The implemented standalone/native-test build is
[`../src/CMakeLists.txt`](../src/CMakeLists.txt). It defaults the shim to
`STATIC` for iOS and `SHARED` elsewhere; callers may override
`FONIX_SHIM_LIBRARY_TYPE` only when the platform packaging plan and exact
symbol-export checks cover the selected type. All runtime profiles require the
exact pinned compatibility-floor ORT headers at compile time.

## 5.6 ONNX Runtime artifact strategy

Prefer official release artifacts where they exactly match the target and EP requirements. Build from an official release tag when:

- the required architecture is absent;
- a minimal/reduced-operator build is required;
- QNN or another EP requires source integration;
- deployment target or page alignment differs;
- sherpa-onnx must be aligned to the same runtime;
- symbols/build metadata needed for qualification are absent.

Do not mix headers from one ORT release with a shared library from another in linked mode. In process mode, compile to the declared compatibility-floor API and negotiate at runtime.

Provider shared libraries and their matching `onnxruntime_providers_shared` library must remain adjacent to the ORT binary. Do not install them globally or rely on `LD_LIBRARY_PATH`/`PATH` as the product configuration.

## 5.7 Reduced-operator/minimal builds

Reduced builds can materially reduce mobile size but create model-specific artifacts. Requirements:

- derive the operator configuration from the exact release models;
- version/hash the model set and generated config;
- use extended minimal build when required by EPs that create kernels dynamically;
- include a full CPU/mobile flavor for general users unless product scope is model-fixed;
- fail model load with diagnostics that identify the reduced-build flavor;
- re-run the complete model suite whenever models or ORT are updated.

Do not label a reduced build as a general ONNX Runtime package.

## 5.8 iOS packaging

Preferred architecture:

- build/package ORT and the shim as a correctly signed static framework/XCFramework or equivalent build-hook code assets accepted by Flutter/iOS tooling;
- use linked runtime mode, not arbitrary `dlopen` paths;
- keep framework/library names consistent across device/simulator slices;
- include arm64 device and arm64 simulator slices;
- add x86_64 simulator only when the selected ORT source/artifact and support policy include it;
- set deployment target to the maximum required by Flutter/Dart, ORT, and selected EP mode;
- link required Apple frameworks for CoreML and other selected capabilities;
- verify no forbidden simulator slice enters the device archive;
- verify code signing, bitcode policy/toolchain expectations, and App Store archive with a sample app.

CoreML's NeuralNetwork format has lower OS requirements than MLProgram; flavor/options must not promise MLProgram on a lower deployment target.

Avoid duplicate static ORT linkage through multiple pods/frameworks. The final link map and symbol table should show a single ORT implementation.

## 5.9 macOS packaging

Support application-local framework/dylib or static linkage according to flavor. Requirements:

- universal packaging only when both architectures are built/tested from the same ORT version/flavor;
- application-local install names (`@rpath`/`@loader_path`) for dynamic libraries;
- hardened runtime and code signing in the sample app;
- CoreML framework linkage for the Apple flavor;
- no global `/usr/local/lib` dependency in release tests;
- verify quarantine/notarization workflow if distributing a standalone application.

For a final Flutter macOS application, run the loader, deployment, identity,
notice, and CPU-inference gate against the `.app` bytes:

```bash
python3 tool/ci/audit_apple_application.py \
  --app build/macos/Build/Products/Release/example.app \
  --platform macos \
  --application-minimum-os 14.0 \
  --reference-runtime /absolute/verified/libonnxruntime.1.27.1.dylib \
  --run-cpu-probe test/fixtures/mul_1.onnx
```

This probe links to the shim framework inside the named application, opens the
fixed sibling ORT framework through bundled mode, and verifies numeric CPU
output. The reference runtime must be the exact lock-verified staged dylib; the
auditor compares its locked size/SHA, UUID, architecture, platform, deployment
floor, and every loaded Mach-O section with the transformed final framework.
It also calls the packaged shim for its full closed build identity. It does not
search a global ORT installation.

The complete reproducible sample path creates a fresh Flutter app, writes the
hook, app-floor, arm64-only, and asset configuration above, builds Release, and
runs the same final audit and CPU probe:

```bash
python3 tool/ci/run_macos_application_gate.py \
  --flutter /absolute/flutter/bin/flutter \
  --artifact-cache /absolute/offline/cache \
  --reference-runtime /absolute/verified/libonnxruntime.1.27.1.dylib \
  --work-dir /absolute/new/fonix-macos-application-gate
```

This gate is pinned to Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` (Flutter
`3.47.0-0.1.pre`) so a toolchain change fails closed and requires new evidence.

The repository also commits [`../example/`](../example/) as the public-API
reference application. Because a native-asset consumer's staging directory
must remain outside the Fonix checkout, its gate copies only bounded regular
source files to a new absolute work directory, patches the local package path,
and rebuilds there:

```bash
python3 -B tool/ci/run_macos_reference_app_gate.py \
  --repository /absolute/path/to/fonix \
  --flutter /absolute/flutter/bin/flutter \
  --artifact-cache /absolute/offline/cache \
  --reference-runtime /absolute/verified/libonnxruntime.1.27.1.dylib \
  --work-dir /absolute/new/fonix-macos-reference-app-gate
```

The gate reproduces the committed manifest/notices byte-for-byte, analyzes and
tests the app, builds Release, verifies the final signatures and hardened
runtime, runs the independent application audit, then launches the named
`CFBundleExecutable` through a bounded one-shot public-API path. The exact
receipt binds the runtime/artifact/model identities, output values, active CPU
provider, full assignment, and double close.

The local Release build is ad-hoc signed. It keeps the sandbox and hardened
runtime but explicitly disables library validation so the separately ad-hoc
signed Flutter framework can load without a common Team ID. The gate verifies
that signed entitlement and reports it as a development exception. It is not
distribution-signing or notarization evidence; an approved distribution build
must establish one consistent signing policy and re-evaluate the entitlement.

## 5.10 Android packaging

### ABIs

Baseline:

- `arm64-v8a` for devices;
- `x86_64` for emulator/testing.

Optional `armeabi-v7a` requires complete dependency support. Do not include obsolete `x86` without a concrete requirement.

### Library layout

Native libraries appear under ABI-specific paths in AAR/APK/AAB artifacts. The final package must be inspected for:

- `libonnxruntime.so` ownership and hash;
- `libfonix_shim.so`;
- sherpa JNI/C API libraries;
- provider libraries such as QNN backends;
- `libc++_shared.so` collisions;
- correct SONAME, `DT_NEEDED`, exported symbols, and ELF alignment.

### Standalone mode

The wrapper may package its pinned `libonnxruntime.so` and shim. It must not also add a Java ONNX Runtime dependency that contributes another copy unless the build is intentionally unified and verified.

### Standalone reference gate

The committed arm64-v8a reference app is built and audited from a clean external
copy with a revision-pinned Flutter SDK, byte-pinned bundletool and ORT inputs,
and version-gated Android SDK, NDK, build-tools, command-line tools, and JDK:

```bash
python3 -B tool/ci/run_android_reference_app_gate.py \
  --repository /absolute/path/to/fonix \
  --flutter /absolute/flutter/bin/flutter \
  --artifact-cache /absolute/offline/cache \
  --work-dir /absolute/new/fonix-android-reference-gate \
  --android-sdk /absolute/Android/sdk \
  --java-home /absolute/openjdk-21.0.12 \
  --bundletool /absolute/bundletool-all-1.18.3.jar \
  --avd-name api35-arm64-avd
```

The gate pins Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534`, command-line tools 20.0,
build-tools 36.0.0, NDK 28.2.13676358, OpenJDK 21.0.12, and bundletool 1.18.3.
It reproduces the committed Android manifest/notices, analyzes and tests the
app, builds an R8-minified development-signed Release APK and AAB, and invokes
`tool/ci/audit_android_application.py` on both final artifacts before any
install.

The auditor and gate require only the `base` AAB module, the exact manifest and
permission/component allowlist, the exact model/manifest/notices, four arm64
libraries (`libapp.so`, `libflutter.so`, `libfonix_shim.so`, and
`libonnxruntime.so`), one ORT owner, closed SONAME/`DT_NEEDED` sets, 67 shim
exports plus `OrtGetApiBase`, complete matching APK/AAB development signatures,
APK `zipalign -P 16`, and static 16 KiB-compatible ELF load segments. Android
stripping changes the ORT whole-file SHA-256, so the auditor separately records
that packaged hash and requires every ordered `PT_LOAD` segment to match the
lock-selected source runtime.

With a named AVD, the same audited APK must report ORT 1.27.1, application-owned
bundled CPU, active provider `cpu`, full assignment, output
`[1,4,9,16,25,36]`, and idempotent double close. The 2026-08-07 run used an API
35 arm64 emulator with a queried 4096-byte page size. API 24 remains only the
manifest/build floor: this receipt does not prove API 24 execution, an actual
16 KiB runtime, a physical device, x86_64, an installed AAB split, XNNPACK,
QNN, or sherpa coexistence.

### sherpa-owned process mode

The wrapper packages only the shim. It must not contribute `libonnxruntime.so`. The shim dynamically resolves the sherpa/application-owned runtime. See document 7.

### Aligned mode

The application/native build packages one ORT built with the selected EPs. Both shim and sherpa link/resolve against it. Use this for QNN/custom builds.

### STL and symbol policy

Minimize exported symbols. If the shim is pure C, avoid a C++ runtime dependency. sherpa/ORT may have their own C++ linkage requirements; inspect `libc++_shared.so` ownership and do not use Gradle collision rules to mask incompatible versions.

### 16 KB page sizes

Native dependencies must be built and packaged for Android 15+ 16 KB page-size devices. CI must inspect ELF load-segment alignment and run a sample app in an appropriate emulator/device configuration. The requirement applies transitively to ORT, shim, sherpa, and provider libraries.

### App bundles

Test the generated split APKs or installed bundle, not only the universal intermediate. Verify each delivered ABI has the complete and singular dependency set.

## 5.11 Linux packaging

- Provide x86_64 and arm64 artifacts where advertised.
- Use `$ORIGIN`-relative RPATH/RUNPATH for application-local dependencies when appropriate.
- Do not require root installation.
- Build against a documented libc baseline or publish separate compatibility variants.
- Audit GLIBC/GLIBCXX symbol requirements.
- Keep provider shared libraries adjacent to ORT.
- Test in clean containers without development packages or global ORT installations.
- Publish detached debug symbols and build IDs.

A system-runtime mode may be offered for managed deployments but must require explicit application configuration and diagnostics. It is not the default consumer experience.

## 5.12 Windows packaging

- Package DLLs adjacent to the executable/application package in a flavor-specific directory supported by secure loader configuration.
- Use absolute paths and safe `LoadLibraryExW` flags.
- Do not depend on the current working directory or mutate global `PATH`.
- Keep `onnxruntime.dll`, provider DLLs, and exact redistributable dependencies aligned.
- Publish x64 as baseline; qualify arm64 separately.
- Test MSIX/unpackaged Flutter desktop as relevant.
- Audit imports and exports with `dumpbin`/equivalent.
- The shim links Windows `bcrypt` and `advapi32` for secure per-run profiling
  names and private-directory ACLs; it must retain no link-time ORT import.
- Include VC runtime requirements or use the project's chosen redistribution policy.
- Sign release DLLs where the product distribution requires it.

## 5.13 Debug symbols and crash diagnosis

For every release artifact:

- strip consumer binaries appropriately;
- retain matching symbols privately or publish them according to project policy;
- embed a build ID/flavor/version;
- generate symbol maps for Apple and PDBs for Windows;
- ensure crash reports can identify exact ORT/shim/provider builds;
- do not strip required provider registration/export symbols.

## 5.14 Artifact verification

Verification is a release gate, not a manual suggestion.

Required checks:

- archive digest and safe extraction;
- expected file allowlist;
- architecture and object format;
- deployment target/minimum OS;
- SONAME/install name/import table;
- `DT_NEEDED`/DLL dependencies;
- exported symbol allowlist;
- ORT/provider adjacency;
- duplicate native library paths;
- Android one-ORT rule;
- 16 KB alignment;
- license/notice/SBOM presence;
- sample application load and inference.

Use the supplied Android inspection templates as a starting point and add analogous platform scripts.

## 5.15 Release reproducibility

A release record must contain:

- source repository revisions;
- lockfile;
- build commands/configuration;
- toolchain/container image digests where used;
- artifact checksums;
- SBOM and notices;
- test/benchmark hardware and results;
- final application artifact inspection reports;
- known limitations and supported flavor matrix.

Bit-for-bit reproducibility may not be possible on every Apple/Windows signed artifact. At minimum, native inputs, unsigned build products, and semantic manifests must be reproducible and independently verifiable.
