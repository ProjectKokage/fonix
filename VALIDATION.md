# Validation Report

- Validation date: 2026-08-08
- Package: `fonix 0.1.0-dev.1`
- Host: macOS 26.5.2 (25F84), arm64

## Result

The declared development API and locally controllable package boundary pass
the completed validation recorded below. The current shared reference source
passes 81/81 application tests and analysis. The separately frozen,
manifest-bound iOS gate source epoch passed its historical 72/72 application
tests, including 25/25 reference-smoke tests and 5/5 iOS project-contract
tests, and its linked simulator application passed two consecutive Debug
builds. The complete iOS gate result is `PASS (2026-08-07)` for that exact
frozen epoch.

This snapshot is not implementation-complete. The Linux x86_64 source path is
implemented but has no exact target-host PASS. Its host gate now requires the
exact `_CS_GNU_LIBC_VERSION` identity returned by the libc loaded into the gate
process instead of inferring libc from executable strings. Local stabilization
now uses an
ordered protocol-v4 worker retirement handshake after malformed replies and
worker-authored terminal failures, and includes `Isolate.spawn` inside the
startup deadline. Sessions and pools now
reserve aggregate input bytes across active and queued work. The native and
standalone Phase-3 runners, Linux `readelf`, POSIX desktop `objdump`, binding
regeneration, macOS runtime inspection, Apple final-application auditor, and
macOS/iOS reference and final-app command paths now share a bounded
trusted-command boundary. It applies independent stdout/stderr byte caps,
incremental strict UTF-8 validation, monotonic deadlines, bounded
process-group TERM/KILL, and direct-child reap cleanup. A successful
direct-child exit is rejected while the new POSIX process group inherited by
ordinary tool subprocesses or an inherited output pipe remains. This is not a
sandbox: deliberate `setsid`/`setpgid` escape is outside the contract. Native
and Phase-3 CTest calls have an exact 300-second per-test timeout inside a
1,800-second suite deadline.

Darwin can briefly return `EPERM` for a process-group record with no signalable
non-zombie member during post-exit teardown. Only that post-exit Darwin state
receives a 50-millisecond monotonic drain-and-recheck window. Success still
requires `ESRCH`. Persistent permission failure or a group that becomes
signalable fails closed; after permission made the reaped group's numeric
identity ambiguous, cleanup does not signal that number.

The Apple, Linux, and Android reference gates call their final-application
auditors in-process so the auditors' bounded tool groups are not nested beneath
an outer session owner. Their former closed serialized-report boundaries are
preserved, and bytecode writes remain disabled across the dynamically loaded
audits. The Android gate binds both the auditor and its shared helper to the
selected repository rather than reusing an ambient module. Apple audit and
gate commands scrub `DYLD_*` loader injection and use a
deterministic locale; binding tools also scrub `LD_*`, and the POSIX desktop
metadata auditor uses a minimal fixed environment. Expected nonzero status
probes are accepted only after clean group settlement, and the iOS plist
converter stages its bounded input in a private temporary file that is removed
after the call. The macOS runtime checker validates exact regular `/usr/bin`
entry points and invokes them with only a fixed system `PATH` and `C` locale,
so caller-supplied Xcode, SDK, compiler, and loader selectors are absent. It
copies the locked runtime once into a private, reverified regular-file snapshot
matching its `@rpath` install-name basename; every Mach-O, export, signature,
and provider check uses that same snapshot.

Windows retains a direct-child timeout fallback and post-completion CTest
inventory bounds; binding regeneration and desktop `objdump` also retain
direct-child Windows fallbacks. Job Object ownership and descendant-tree
cleanup remain deferred and unclaimed. This is not a claim that every
repository subprocess is an untrusted-code sandbox.

The macOS gate applications and independently audited Android arm64-v8a
Release APKs load and execute their exact packaged CPU paths. A separate
Android profile build also passes the closed XNNPACK functional
assignment/parity/fallback/recovery path on the named API 35 arm64/4096-byte
emulator. The source-final sherpa-owned APK passes the complete trusted
four-record matrix: both load orders on named API 35 arm64-v8a/4 KiB and 16 KiB
emulators, with runtime negotiation, two alternating cycles, cancellation,
stale suppression, recovery, both disposal orders, and cleanup bound to the
installed APK.

This is not redistribution, five-platform, unqualified `Release-ready`, or 1.0
approval, transfer to an untested Android tuple, or XNNPACK provider
qualification. The package remains `publish_to: none`, and the external
licensing, signing, target-host, physical-device, performance, and provider-
qualification boundaries listed below remain authoritative.

The repository now validates a separately supplied scoped pre-1.0 candidate
bundle against the frozen scope, exact evidence inventory, out-of-band bundle
digest, and five detached approval domains. No external candidate bundle,
evidence directory, signatures, or approvals have been produced for this
snapshot, so this is a validation capability rather than a readiness claim.

This checkpoint also adds deterministic, reviewable records for the resolved
public Dart API and structural native C ABI. The Dart record is reconstructed
only by Dart 3.11.5 with analyzer 14.1.0 and fails closed on unsupported source
patterns or constants before comparing canonical bytes. The C record binds the
complete parsed header/export surface and the isolated native audit invocation
contract. A matching record exposes declaration drift for review; it neither
classifies compatibility nor supplies the detached API/ABI approval or any
target-execution evidence.

The current shared-runtime hardening checkpoint bounds isolate string tensors
to the native element/content limits, charges empty-string retention and
requested output names against worker messages, and uses a later valid ready
port only for acknowledged cleanup after malformed startup ownership. Native
value-graph validation is read-only once a value is published; an unconditional
two-thread fake-runtime regression now runs in normal, Address/UndefinedBehavior,
and dedicated ThreadSanitizer modes, with a separate Linux x64 TSan workflow.
If native profiling disable fails, run-options reuse is rejected before another
native `Run`; finish may be retried, and disposal releases the native owner
before removing the retained private directory. The Linux TSan workflow was
not executed from this macOS host; the same 17-test TSan suite passed locally
with AppleClang and the exact pinned ORT.

The follow-up portable-lifecycle checkpoint orders worker errors and exit on a
single lifecycle channel, waits for worker-authored startup retirement, and
holds active input reservations until observed exit after an uncaught failure.
The native cancellation registry executes ORT termination set/unset calls
outside its process-wide lock while serializing same-token settlement,
preserves retry ownership after either native failure, and permanently
exhausts rather than wraps its monotonic token space. Strict JSON now enforces
the exact configured container depth for empty leaves and passes 4,096
deterministic generated mutations. Provider discovery and model/custom
metadata fault injection prove that bounded partial ORT owners are released on
error without allowing corrupt oversized counts to authorize entry iteration.
The integrated exact-ORT native suite passed 17/17 in each of normal,
Address/UndefinedBehavior, and ThreadSanitizer modes on this host. Apple
LeakSanitizer remains unavailable, and the prebuilt ORT binary itself is not
sanitizer-instrumented.

The bounded seeded-lifecycle follow-up ran on macOS arm64 with exact Dart
3.11.5. Seeds `5`, `7`, `1`, `3`, `17`, `2`, `15`, and `4` select eight unique
explicit-gate traces: two graceful-close queued positions, two crash queued
positions, and all four queued-position by active success/error pool
orientations. The corpus used 12 real Dart isolate harness workers and 66
recorded actions; each trace was capped at two workers, four submitted runs,
nine actions, and 52 aggregate reserved input bytes. It rejected overfill,
settled queued and active cancellation, awaited all public settlements and
idempotent closes, and finished with zero Dart run/input reservations, closed
sessions, and one controller connection-close initiation per worker. Two
additional workers admitted request IDs `INT64_MAX - 1` and `INT64_MAX`, then
proved permanent zero-capacity exhaustion and retriable pool backpressure when
the other peer was live but saturated; a separate oversized request failed
closed when only the exhausted peer could ever fit it. Cleanup releases a held
synthetic gate before awaiting close, so an assertion failure retains its
seed/action trace rather than timing out during teardown. The synthetic traces
do not prove native-owner leak freedom, target lifecycle behavior, throughput,
RSS, or platform support; the same focused run separately exercised the strict
fake cancellation registry
and lock-pinned exact ORT 1.27.1 worker.

The native-allocation checkpoint routes wrapper-requested heap, aligned, and
returned canonical-path owners through an internal seam whose fail-Nth
activation exists only in the POSIX test build. Its portable operation matrix
now injects 76 success-path failures: the prior 63 build-manifest, runtime,
provider, run-options/profiling, session, external-data, tensor, and composite
points plus eight synchronous-run and one each run-result,
cancellation-registration, buffer-lease, tensor-lease, and tensor-info points.
A separate three-point status target proves that status-object OOM uses the
static allocation emergency while operation/message-copy OOM preserves the
original domain, wrapper code, and ORT code with an ownership-safe static
fallback. Each failure left its public output neutral and permitted an
immediate clean retry. Fake owner guards made run-options,
external-initializer, metadata, composite, and returned-run-output retries
conditional on prior partial-owner release. A separate provisioned ORT 1.27.1
matrix injected 50 failures across dense, nonempty-string, ZipMap
sequence/map, optional Some/None, string-element, and tensor-info paths, with a
count-plus-one success sentinel for every operation. The exact harness began
under hostile inherited fault variables and cleared them before its first shim
call. Another fake ORT `Run`
returned one owned output with an error; the shim released it exactly once,
preserved the ORT-domain run failure, then completed and disposed a successful
recovery run.

The same POSIX suite rejected a renamed-and-replaced private profiling
directory without reading forged bytes or touching the replacement, withheld
output on permission loss and an unexpected nonempty child, gave cleanup
failure status precedence, retained the exact owner while blocking reuse, and
retired it after an explicit repair-and-finish retry. A synchronized retry
window continued rejecting concurrent native Run, and an injected private-child
descriptor failure under `umask(0700)` left the application root empty; a
successful cycle under that umask repaired the private child to exact `0700`.
The root check rejected a final symlink even with a trailing separator. An
exact-name FIFO was rejected without blocking or publishing bytes and was
retired completely. Exact filename, descriptor, device/inode, link-count,
regular-file type, size, bounded-read, and checked cleanup conditions precede
evidence publication. The 15-test source POSIX suite and its
Address/UndefinedBehavior and ThreadSanitizer variants passed on this host;
Apple LeakSanitizer remains unavailable.

The provisioned 17-test native suite used the exact locked macOS arm64 ORT
1.27.1 and now includes a real CPU run-options profile cycle. It accepted the
runtime's exact `profile_YYYY-MM-DD_HH-MM-SS_mmm.json` basename, returned a
non-empty JSON event array from the deliberately poisoned output, and left its
private artifact root empty. This is exact-host native evidence, not a Windows,
Linux, Android, or iOS target-host claim.

The CPU measurement checkpoint adds a reproducible 4.29-GFLOP float32 MatMul
fixture and an exact opt-in macOS/Linux final-application path over the public
synchronous API. A fresh macOS arm64 Release build passed the current path with
asset rehashing, bounded stabilization, 100 warm samples, three one-second
throughput windows, exact finite float32 bit checks after every run, strict
one-node CPU assignment outside timing, eight total-process RSS samples,
double disposal, and temporary-profile deletion.

The follow-up protocol-v2 collector launches exactly five fresh challenge- and
PID-bound processes, retains every raw target fragment plus one raw host
sidecar, observes one canonical application tree unchanged around the launches,
and derives a collection-schema-2 record. A separate offline validator reopens
that exact seven-file bundle and independently rederives the collection before
emitting a schema-1 `offline-consistency-only` validation record. An earlier
macOS arm64 Release collection and replay passed with `incomplete`
comparability under the unavailable macOS power/thermal observer.

The current macOS observer reads the public `NSProcessInfo` thermal enum and
low-power boolean, cross-checks low-power state against bounded `pmset`
active-source and configured-profile output, and publishes only a
domain-separated digest of that profile. Focused tests cover the API mapping,
closed power sources, active-profile selection, digest stability, malformed and
contradictory data, platform-specific grammar, derivation, and independent
replay. Apple `nominal` can also mean the thermal state was undetermined, a
stable elevated state still requires review, and the configured `pmset` profile
does not establish every dynamic power assertion. `baseline-comparable` means
only that the recorded inputs are complete and stable enough to match in a
later review; it is not thermal-health or baseline approval. Linux thermal
observation remains unavailable. This source checkpoint contains no controlled
five-launch bundle from the new observer; any later raw collection and offline
replay are external evidence.

The raw fragments are not independently authenticated, supplied native members
are packaged inputs rather than exact loaded-byte proof, repository evidence is
not compiled provenance, and neither record is a baseline, approved threshold,
performance claim, provider qualification, platform support decision, or
release approval.

## Implemented boundary

The checked source contains the complete app-owned wrapper layer described by
the active documentation:

- a stable 67-export C ABI over ONNX Runtime API 27 with closed status,
  runtime, session, value, composite-value, run, cancellation, profiling, and
  data-lease ownership, including read-only published-value validation and
  fail-closed retirement of unresolved native profiling state;
- a typed Dart API for trusted runtime sources, file/byte/external-data models,
  dense tensors, strings, float16/bfloat16 storage, sequences, maps, optionals,
  copied metadata, native-backed leases, and named synchronous inference;
- canonical public-Dart-API and native-C-ABI review records, reconstructed in
  minimum-SDK CI before binding regeneration, with exact nominal type,
  constant/default, annotation, redirect-chain, header, preprocessor, export,
  and native-auditor integrity boundaries;
- bounded worker-isolate sessions and pools using wire protocol version 4,
  paused-spawn parent ownership, early authoritative command-port transfer,
  correlated readiness, cooperative cancellation, graceful timeout cleanup,
  malformed-ownership cleanup-only recovery, stale-result suppression,
  string/output-name-aware backpressure, and idempotent shutdown;
- ordered CPU, XNNPACK, CoreML, legacy NNAPI, QNN, CUDA, TensorRT, DirectML,
  OpenVINO, oneDNN, and MIGraphX configuration with discovery, registration,
  per-run assignment evidence, fallback enforcement, and scoped CoreML cache
  identity; and
- five-OS build-hook paths, exact offline artifact resolution, final-package
  auditors, Android single-ORT ownership and sherpa/QNN evidence tooling,
  deterministic fixtures, benchmark-receipt validation, a public-API CPU
  target measurement fragment, and closed source and release-evidence checks,
  plus an exact scope-only pre-1.0 policy validator and a separate external
  candidate-approval validator that requires 55 exact evidence slots and five
  detached approval categories without weakening global five-platform release
  semantics;
- a shared bounded-command helper for trusted POSIX native, Phase-3,
  Linux/desktop audit, binding, macOS runtime, and Apple application/gate
  commands, with online per-stream caps, strict UTF-8, monotonic deadlines,
  process-group TERM/KILL, direct-child reap cleanup, and residual-group or
  output-pipe rejection; deliberate process-group escape and Windows Job
  Object ownership remain outside that helper's contract;
- a committed iOS arm64, macOS arm64, Android arm64-v8a, and Linux x86_64
  Flutter reference app over the public Fonix library, with bounded worker
  ownership, exact
  model/runtime identity, cancellation, retry, lifecycle replacement,
  stale-result suppression, deterministic cleanup, and platform-specific
  final-package gates; the Linux source adds a relocatable `$ORIGIN/lib`
  layout, closed GNU-versioned exports, and a strict final-tree/ELF/provenance
  auditor, but has no target-host PASS. The iOS path uses linked runtime
  ownership and a resident, challenge/PID-bound receipt. Its app-owned native
  launch channel
  exposes only a cached null-or-closed activation to Dart, which revalidates it
  with a bounded wait and never receives the raw process environment. The
  Android one-shot mode has separate exact CPU and XNNPACK profiles, with the
  latter owning strict
  assignment, exact CPU parity, explicit fallback report/rejection,
  post-rejection recovery, and deterministic five-session cleanup; and
- a separate sherpa-owned Android arm64-v8a reference scaffold with
  deterministic bounded Fonix/VAD fixtures, real process-runtime Fonix and
  sherpa adapters, one authoritative lifecycle publisher, active native
  cancellation and stale-result suppression, recovery and disposal checks,
  and a trusted UID/PID-bound one-tuple target runner. Its runtime-provisioned
  staged gate binds the complete hosted dependency trees, generated plugin
  graph, all eight qualification assets, raw native inputs, and one exact R8
  Release APK/base-only-AAB static pair. That exact APK has separate trusted
  4 KiB and 16 KiB captures for both load orders and one aggregate
  compatibility manifest; the validator and aggregate records remain
  explicitly offline-consistency-only.

Sparse tensors, opaque/custom values, arbitrary provider plugins, training,
GenAI, and Web/WASM remain explicitly outside this API rather than unfinished
paths.

## Final verification summary

| Gate | Result |
| --- | --- |
| Dart formatting | Full source tree, 0 changes |
| Dart analysis | No issues with `--fatal-infos` |
| Ordinary offline Dart suite | 260 passed, 18 explicitly provisioned skips on exact Dart 3.11.5 |
| Provisioned Dart suite | 298 passed, 8 explicitly provisioned skips with the freshly built shim, strict fake runtimes, and exact ORT 1.27.1 supplied |
| Public Dart API baseline | 38/38 focused tests passed; 101 exports reproduce at contract SHA-256 `0249614ad3ccf9b18492fe2573c09ef06f0bc045f015de4e8db43567d363dba7` and canonical file SHA-256 `3aad3a9682ade05397b553697f55d4d83336aab2e822e2c182be0f620ebea5d1` |
| Native C ABI baseline | 16/16 focused tests and the 15/15 source POSIX CTests passed; 67 functions, 9 constants, 8 enums, and 8 structs reproduce at contract SHA-256 `50612f5c51070212216aafb68e1ca5e2447acbd18dcdbf3608bdf9aa69128c20` and canonical file SHA-256 `02985575cc126370feb2a736ee0fca334e741d1d11da9437d4fa86aef9abab6c` |
| Worker lifecycle and strict JSON with strict fake and exact ORT | 55/55 focused tests passed, including eight unique bounded composed lifecycle traces, positive-int64 request-ID exhaustion, ordered fatal exit, acknowledged startup retirement, exact empty-container depth, and 4,096 deterministic mutations |
| Shim allocation and partial-output fault injection | 76/76 portable operation points, 3/3 status-construction points, and 50/50 provisioned exact-ORT branch points passed with neutral outputs, partial-owner release, exact count sentinels, and clean retry; status field OOM preserved original numeric diagnostics; ORT error-plus-owned-output cleanup released exactly once and recovered on the next successful run; POSIX profile replacement, restrictive-umask, nonregular-file, and cleanup failures published no bytes and retained or retired ownership as required |
| Native FFI bridge with strict fake runtimes | 11/11 passed |
| Exact ORT core Dart inference | 8/8 passed |
| Exact ORT generated Phase-3 Dart corpus | 18/18 passed |
| Exact ORT CPU/CoreML run-evidence suite | 3/3 passed |
| Python CI-script tests | 807/807 passed after regenerating the closed source manifest; the 110-test CPU collection subset also passed independently |
| Scoped pre-1.0 policy validator | 21/21 focused tests passed; canonical path-free scope-only record SHA-256 `77afac75f64af4df4156e7c60b9fd291d32fc51f41bcdc3cff949de070ed5c58` binds 4 selected targets, 5 compositions, 4 unsupported targets, and 2 exact deferrals without asserting readiness |
| Source-release archive validator | 25/25 focused tests passed; exact Git-tree and manifest closure, ZIP and gzip/tar structure, revision rebinding, verified-source loading, retained-descriptor ownership, malformed input, tamper, bounds, and deterministic publication are covered without extraction |
| Scoped candidate approval validator | 33/33 focused tests passed; exact 48 composition and 7 shared evidence records, 5 approval domains, schema parity, same-descriptor source-archive closure, hostile helper isolation, path and identity confinement, publication races, tamper, bounds, and deterministic blockers covered |
| POSIX bounded-process helper | 19/19 focused tests passed; direct-child PID observation and observer-failure group retirement are included, and all integration contracts are covered by the 807-test Python suite |
| macOS runtime checker focused tests | 10/10 passed; exact-ORT inspection also passed under a hostile parent selector environment |
| Linux final-app auditor/reference-gate focused tests | 39/39 passed, including exact loaded-glibc identity and malformed/unavailable identity rejection; source-side synthetic coverage only |
| Android reference-gate focused tests | 37/37 passed; source-side orchestration coverage only |
| Android load-order receipt validator | 30/30 focused tests passed; the exact transitive native-library verifier is pinned before its captured bytes can execute |
| Apple final-application auditor focused tests | 54/54 passed |
| iOS reference-gate focused tests | 97/97 passed |
| macOS reference/final-app gate focused tests | 32/32 passed |
| Standalone Python verifier tests | 62/62 passed |
| C source quality | 38/38 files passed the closed byte/style gate |
| FFI binding regeneration | Both generated bindings reproduced exactly |
| Deterministic fixture inventory | 23 files and 23 closed model/data paths |
| Deterministic XNNPACK MatMul fixture | Generator check and exact-ORT packaged-asset identity/inference test passed |
| Deterministic CPU benchmark MatMul fixture | 8/8 generator tests, deflated Android package audit coverage, and exact-ORT packaged-asset identity/inference passed for the 4.29-GFLOP model, input, and exact reference output |
| Exact ORT standalone fixture CTests | 2/2 passed |
| Native POSIX CTests with exact ORT | 17/17 passed |
| Native POSIX ASan+UBSan CTests with exact ORT | 17/17 passed; Apple LeakSanitizer unavailable |
| Native POSIX ThreadSanitizer CTests with exact ORT | 17/17 passed locally with AppleClang; the required Linux x64 TSan workflow remains pending CI-host execution |
| Adjacent/bundled-loader CTests | 5/5 passed, including Android arm64/x86_64 APK namespace paths and desktop regressions |
| Five-artifact offline audit | 8/8 passed |
| macOS/iOS build-hook suite with exact archives | 28/28 passed |
| Android API-24 build/audit suite | 4/4 passed |
| Android Gradle dependency-input metadata | Standalone and sherpa-owned macOS Release graphs regenerated identically from two independent fresh stages; strict offline `assembleRelease bundleRelease` replay passed after cache provisioning |
| Android Gradle gate invariant | 105/105 focused common, standalone, and sherpa gate tests passed, including metadata policy/identity/tamper, guarded build/report orchestration, and hostile inherited JVM/Gradle environment overrides |
| Linux x64/arm64 and Windows x64 Zig cross-build | 1/1 passed |
| Flutter asset publication | 7/7 passed |
| Fresh final macOS Flutter application gate | Passed, including packaged CPU inference |
| Committed macOS public-API reference application gate | 72/72 app tests and analysis, Release build, exact full-CPU receipt, double close, and independent final-app audit passed |
| Committed Android arm64 public-API reference gates | 40 app tests per clean profile build; development-signed R8 Release APK/AAB audits; unchanged CPU receipt; and closed XNNPACK assignment/parity/fallback/recovery receipt on the exact API 35 arm64/4096-byte emulator passed |
| Current shared public-API reference source | 81/81 app tests and analysis passed in a clean external copy |
| CPU collection-v2 and offline-replay tooling | 110/110 focused tests passed: 19 bounded-process, 22 derivation/core, 41 collector, and 28 independent-validator tests, including the macOS API/`pmset` observer contract, platform-specific host grammar, closed platform stderr, spaced packaged basenames, tamper, replay, alias, mutation, collision, retained-inode, parent relocation, partial-publication, and durability failures |
| Current macOS arm64 CPU measurement path | An earlier fresh Release application passed five challenge/PID-bound processes, exact seven-file raw publication, collection-schema-2 derivation, and independent schema-1 offline replay with `incomplete` comparability; this source checkpoint contains no controlled bundle from the current observer, any later collection/replay is external evidence, and no baseline is claimed |
| Committed iOS arm64 public-API reference gate | Frozen source epoch: 72/72 app tests and analysis; unsigned device Release `.app` build/static audit; two consecutive simulator Debug builds; install-transport-bound CPU/full-assignment receipt, process settlement, and uninstall: `PASS (2026-08-07)` |
| Source-final sherpa-owned Android arm64 package and 4 KiB/16 KiB target gates | 50 staged Flutter tests, analysis, locked offline resolution, exact eight-asset audit, R8 Release APK/base-only-AAB build, hosted-package/native-input guards, and closed package-pair audit passed; trusted `dart-first` and `sherpa-first` captures passed on the exact API 35 arm64-v8a/4096-byte and 16384-byte emulators, with verified uninstall after every run; the four-record compatibility manifest was generated |
| Closed source checksum manifest | The current API/ABI checkpoint reproduces exactly after regeneration. Historical Android records retain their commit-bound manifest; the iOS gate passed with frozen manifest SHA-256 `9c790a572f9876262496ffc47f85e2cbc4c027a9e3846cdd43c5d5489a14f23a` and source-tree SHA-256 `2c275c655d3569ec767f36d9e99522b5e38ba937e6236666abe0795ea43a03a2` |

The ordinary Dart skips are environment gates, not ignored failures. Their
real-runtime, fake-runtime, exact-archive, cross-build, and CoreML cases were
run separately above wherever this host had the required inputs.

## Exact runtime and fixture evidence

The real-runtime tests used the lock-pinned ONNX Runtime 1.27.1 macOS arm64
CPU archive with SHA-256
`e42b77a7281cc6e55141bf44fcfbac2c782b823a491bbb6ac33c781dd991f8a6`.
The generated Phase-3 manifest SHA-256 is
`8f0eb39e377a5308f46c6ae6264c223fc04e8b108eea3b257da57c196f09c759`.

The deterministic corpus covers scalar float64, symbolic Add and MatMul with
multiple concrete shapes, zero-length float32, bool normalization, every
wrapper-supported fixed-width integer type, strings, float16/bfloat16 raw
storage, copied metadata, unknown rank, sequences, string/int64 maps,
present/empty optionals, and explicit external data. The inventory verifier
allows only generator-owned `.onnx`/`.bin` paths plus the independently pinned
130-byte upstream `mul_1.onnx`; it rejects stale files, links, nonregular
entries, missing paths, and bounded-walk violations.

## Native and packaged evidence

Native compilation used Apple Clang 21.0.0, CMake 4.4.2, and warnings as
errors. The normal, ASan+UBSan, and TSan suites each passed all 16 CTests
against the exact runtime; Apple Clang does not provide LeakSanitizer on this
host. The dedicated Linux x64 TSan job is checked in but was not executed from
this macOS validation. The bundled loader passed adjacency, Flutter-framework,
unexpected-layout, and closed Android arm64/x86_64 APK-namespace tests,
including malformed paths and missing runtime/symbol failures.

The bounded-command changes were verified at the Python helper, auditor, and
runner-contract levels. The first native-runner slice was also exercised on a
macOS arm64 host through the POSIX native source harness, where configure,
build, inventory, and 12/12 CTests passed. Binding regeneration reproduced both
checked-in outputs exactly through the new command owner. Both full provisioned
macOS application gates then passed at source commit
`bb78c333160d9ccc3963497fcbe1ddcf23edeb06`, and the complete iOS
device-build/simulator gate passed at source commit
`3716a4cc706f3186e1dafac4dc0ee6eeef1823cf` through the migrated
bounded-command and in-process-auditor paths. None of these checks proves
containment of a deliberately escaping process, distribution readiness, or a
broader platform support claim.

The hardened macOS runtime checker separately passed against the exact pinned
38,502,216-byte ORT dylib while the parent supplied hostile Xcode, SDK,
compiler, loader, and search-path selectors; the child tool environment omitted
them, and the single private snapshot passed byte, Mach-O, dependency, export,
provider, and embedded-signature checks. This is runtime-artifact inspection,
not final-application evidence by itself; the subsequent macOS application
gates provide the separate refreshed final-bundle and inference evidence.

The offline artifact cache contained the exact lock-selected macOS arm64,
iOS arm64 XCFramework, Android arm64-v8a/x86_64, Linux x86_64/arm64, and
Windows x64 archives. Static auditors rehashed source and staged bytes and
checked closed inventories, notices, architectures, dependencies/imports,
SONAME/install-name/RUNPATH policy, Android 16 KiB layout, provider adjacency,
and the shim's no-ORT-dependency contract where required.

Android shims were compiled at API 24 for arm64-v8a and x86_64 with NDK
30.0.14904198 (Clang 21.0.0) under both application-owned bundled and
sherpa-owned process modes. Zig 0.16.0 cross-built the Linux x64/arm64 glibc
2.27 and Windows x64 outputs, including the Windows `bcrypt` and `advapi32`
system-library contract. These are cross-build and static package-layout
results, not target-device execution claims.

The 2026-08-07 sherpa input audit used Kokage's exact locked federated
`sherpa_onnx` 1.13.4 Android package directories, not a synthetic JNI AAR. The
release is mapped to full source revision
`142807252687d81b40d6315f23470a1512a00de3`. The exact hosted archive
SHA-256 values accepted by the current receipt validator are:

- `sherpa_onnx`:
  `889c03cf7a8788795e3a6d35bf9f20b66d1862ea7a73a1b6aaf6e450715c870a`;
- `sherpa_onnx_android_armeabi`:
  `9e96729d99567c3f64fc6f4c232ef04b57ae4ef3bf1b21364ee6e461103cb7cf`;
- `sherpa_onnx_android_arm64`:
  `0337650bc2357f39b751f1b9ced37770de3b60026effe66473b557346b4e3f97`;
- `sherpa_onnx_android_x86`:
  `6eaa462a24bf881c8ee2b3ab5b9fad3d310b714fc9d3ac4b9f650d0a90c9d0ab`;
- `sherpa_onnx_android_x86_64`:
  `181aa0f0968adf2cf2dcb369c879ea372653864538b82772877e22748a80254f`.

These are hosted-package provenance values, not native-library inventory
digests. The exact audited native inputs were:

- arm64-v8a ORT `994848008526a934dfb579ac773b00e5867929234852b061005d45aacaee9533`,
  C API `cb0fe5f4d26e8f66a5466cfc760caafaf50c60128321e491f538d60857324f56`,
  and C++ API `f961acd4fc2582ed8bea395c941e8c7b51fd8b41cffb2855779103764d6e7247`;
- x86_64 ORT `7144a2015ce495677420b287cef908eb3d379e09913b5e715aa48826b2080f2d`,
  C API `7922e28904bd84f71932d81a3e44753041d7967ad3b2db1a68ec26cba6eee855`,
  and C++ API `e52ba5fdb4eb297381d51e8cc0e4dc616dd23b9812a285b63128f7284b91a2b6`.

Each ABI independently passed `sherpa-audit` with the explicit `flutter-ffi`
profile, exact C API/C++ API dependency graph, single ORT/libc++ ownership
checks, and static 16 KiB alignment. This proves the selected source native
inventories only. It does not prove which bytes Gradle selects, final-package
coexistence, Bionic load order, or alternating inference.

The accompanying schema-2 receipt validator and schema-2 compatibility
generator retain synthetic contract/tamper coverage. Four real raw receipts
now pass the validator, covering both load orders on independent 4 KiB and
16 KiB target runs, and they produce the required compatibility manifest. The
validation and aggregate records remain `offline-consistency-only` with
unverified target provenance: public tool hashes do not authenticate the
source of target or logcat JSON. The separately retained trusted-runner capture
manifests provide the target-capture provenance for all four exact tuples.

## macOS application and provider evidence

The final application gate used Xcode 26.6 (17F113) and Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` (Flutter 3.47.0-0.1.pre, Dart
3.13.0-282.1.beta) from source commit
`bb78c333160d9ccc3963497fcbe1ddcf23edeb06`. It created a fresh Release
application at a macOS 14.0 floor, audited the packaged manifest/notices and
arm64 Mach-O identities,
verified code signatures and loaded-section parity with the lock-selected
runtime, then loaded only through the packaged shim/framework layout and
produced the expected CPU outputs.

The separate committed-reference gate copied `example/` outside the package
checkout, reproduced the exact manifest/notices, passed 72/72 controller,
platform-contract, channel, smoke, backend, and widget tests plus analysis,
built Release, and verified
signatures, hardened runtime,
the sandbox, and the explicit local ad-hoc library-validation exception. The
independent final-application audit ran before the named Flutter executable was
launched directly. The packaged public-API path then reported ORT 1.27.1,
wrapper-owned bundled CPU,
the exact 130-byte model SHA-256, output `[1,4,9,16,25,36]`, active provider
`cpu`, full CPU assignment, and an idempotent double close. The gate removed
all `DYLD_*` overrides and bounded its copy, commands, output, receipt, and
runtime.

The same exact runtime produced validated one-run assignment evidence for CPU
and CoreML with `MLComputeUnits=CPUOnly`, output parity, scoped cache identity,
and worker-isolate transport. CoreML required normal host filesystem access to
create its separate OS-managed compilation workspace. This proves neither GPU
nor Apple Neural Engine execution.

## iOS arm64 reference application evidence

The iOS gate is pinned to macOS 26.5.2 (25F84), Xcode 26.6 (17F113), the
iPhoneOS and iPhoneSimulator 26.5 SDKs, and Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` (Flutter 3.47.0-0.1.pre). Its
offline cache contains the 135,152,698-byte
`microsoft.ml.onnxruntime.1.27.1.nupkg` archive with SHA-256
`9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383`,
plus the exact macOS host archive needed by host-side Dart commands. The gate
freezes the current closed source manifest into a private source epoch and
derives both application variants from that same tree.

The passing frozen epoch came from source commit
`3716a4cc706f3186e1dafac4dc0ee6eeef1823cf`, contained 401 files and
7,358,160 bytes, and had manifest SHA-256
`9c790a572f9876262496ffc47f85e2cbc4c027a9e3846cdd43c5d5489a14f23a`
and closed tree SHA-256
`2c275c655d3569ec767f36d9e99522b5e38ba937e6236666abe0795ea43a03a2`.

The committed application and hook declare iOS 15.1 and arm64 only. Its four
closed Mach-O images are `Runner`, `App.framework/App`,
`Flutter.framework/Flutter`, and `fonix_shim.framework/fonix_shim`. The audit
binds the application identifier `dev.fonix.fonixReference` and
requires exact arm64 subtype, file type, header flags, platform, deployment
floor, dylib identity, dependency kind/path/version, load-command inventory,
RPATH, and shim nlist/dyld export profiles. `Runner` and the Fonix shim have a
15.1 load-command floor; the pinned Flutter `App` and `Flutter` frameworks have
their exact 15.0 floors beneath the application-level 15.1 contract.

The complete gate result is `PASS (2026-08-07)`. Its device branch
builds Release with `--no-codesign`. The root bundle and executable are
unsigned, contain no provisioning profile, and the three nested frameworks
carry exact verified teamless ad-hoc signatures. The result is `static-only`:
the application is not installed or executed on a physical device.

The simulator branch builds Debug twice to exercise the idempotent RPATH
normalization and audits the resulting arm64-only application with strict
development signatures. Before launch, its install-transport identity requires
the installed application to retain every directory, file path, byte, and
`Runner` executable bit from the audited tree. It permits only the exact
`simctl` normalization observed on this pinned tuple: executable `true` to
`false` for `App.framework/App`, `Flutter.framework/Flutter`, and
`fonix_shim.framework/fonix_shim`. On the iOS 26.5 arm64-capable iPhone 17 Pro
simulator, a fresh 256-bit challenge and exact launch PID bind the resident
receipt. On this pinned tuple, the native process received the `simctl` child
environment while Dart `Platform.environment` did not expose those launch
values; the application delegate therefore validates the exact two-value
activation and forwards only its cached closed result over an argument-free
app-owned channel. Dart revalidates that response within five seconds. The
public receipt reports ONNX Runtime 1.27.1, linked wrapper-owned
CPU, the exact 130-byte model, output `[1,4,9,16,25,36]`, active provider
`cpu`, full CPU assignment, and idempotent double close. The public report
retains only hashes of the launch challenge and PID. It then verifies process
settlement, install-transport ownership, uninstall, and package absence.

The final Mach-O audit proves that the closed bundle contains no separately
packaged raw Mach-O or audited load-command dependency attributable to ONNX
Runtime. Validated native-assets hook metadata, embedded schema-3 identity,
dyld exports/fixups, and normalized runtime fields bind the packaged shim to
the resolver-built prepackage output across the exact install-name,
local-symbol-strip, and code-sign/linkedit transformations. This does not prove
the absence of runtime `dlopen`, another static ONNX Runtime copy in a
different Mach-O, or that the selected static archive was linked exactly once.

The report preserves that boundary in `linkedRuntimeIdentity` through
`runtimeMode`, `packagedShim`, `referenceShim`, `hookInvocationMetadata`,
`normalizedRuntimeFields`, `normalizedRuntimeFieldsSha256`, `comparisonScope`,
`accountedTransformations`, `embeddedBuildIdentity`,
`nlistAndDyldExports`, `separatelyPackagedOrtMachOs`,
`auditedOrtLoadCommandDependencies`, `runtimeDlopenBehavior: not-proved`,
`otherMachOStaticOrtCopies: not-proved`,
`staticArchiveMultiplicity: not-provable-from-final-bundle`, and the explicit
`claimBoundary`.

This checkpoint produces no physical-device execution, provisioning, approved
device or distribution signing, IPA, App Store, or distribution evidence. It
also produces no iOS CoreML, XNNPACK, GPU, or Neural Engine assignment,
performance, memory, thermal, sustained-behavior, or provider-qualification
evidence. The simulator receipt is exact Debug-tuple evidence and does not
transfer to simulator Release or another device/runtime tuple.

## Android Gradle dependency-input integrity evidence

The standalone and sherpa-owned Android Release graphs now have separate
Gradle dependency-verification metadata generated on the macOS arm64 host. The
metadata enables Gradle metadata verification and contains only SHA-256
checksums, exactly one per artifact, for its closed component/artifact
inventory. The sherpa graph was generated with its current
`noCompress += "bin"` Release configuration:

- `example/android/gradle/verification-metadata.xml`: 261,471 bytes,
  552 components, 1,000 artifacts, SHA-256
  `607bf17e59fccff1efe6e763784b1c1f8a93a5d091b318df7e7a4afc5f239b28`;
- `templates/android/sherpa_reference_app/android/gradle/verification-metadata.xml`:
  311,562 bytes, 660 components, 1,197 artifacts, SHA-256
  `8104e11722a14d5980075f417133b1ca30d822446cbb959dbae97df5b9749366`.

Each graph was regenerated from two independent fresh external stages and
produced identical metadata bytes. After its Gradle cache was provisioned, a
separate disposable staged copy of each graph passed:

```bash
./gradlew --offline --no-daemon --dependency-verification strict \
  assembleRelease bundleRelease
```

Clean strict direct-Gradle rebuilds in both fresh stages also passed on this
same host and cache. The standalone AAB reproduced at 27,296,845 bytes with
SHA-256
`5ff43b422d9642714bf615929de684562ddc320448e76d1b8146fddf1f4a8a88`;
the sherpa-owned AAB reproduced at 26,411,976 bytes with SHA-256
`a82b690fb029123b4b54d71abf8a36a57cbd37798581026b7e54c0d93ee88f7c`.
The corresponding APKs did not reproduce byte-for-byte. Each A/B pair had the
same size and identical ZIP member names and contents—70 entries for the
44,863,501-byte standalone pair and 60 for the 43,119,633-byte sherpa pair—but
bytes within the 8,192-byte APK Signing Block differed. The standalone A/B
SHA-256 values were
`d87fc3f6bc32a33e2a87549640f340fdb368cb59e750afe4791bc915ba6562e9` and
`43c696ff7461396e1dd05ba92aedb802feac6bc5f06ab9922ce9b8565ef7ae8c`;
the sherpa A/B values were
`6ddc8f895282ee15ea175546c9adf34d381294180f3ba12d8f7462f0aead2842` and
`efe45412e3b98423fe65bda4e7994f693aaceed2f47ebb1cb101996ae8521735`.
This is a bounded same-host observation, not a signed-APK reproducibility
claim or a substitute for release-signing evidence.

Gradle-wrapper/bootstrap resolution was not offline. The Flutter APK/AAB build
commands are not claimed offline. The controlled gate source now requires the
closed metadata in strict mode, removes the named inherited JVM-option
variables and verification-specific Gradle project-property override, then
sets a gate-owned strict system property. It binds the exact metadata identity
before and after both package builds and includes that identity in its report.
The current gate assumes a non-hostile local Gradle user home and init-script
environment; it does not claim to sandbox a hostile build host.

A real tamper probe replaced the recorded Android Gradle Plugin 9.1.0 JAR
checksum with an incorrect value. Strict mode rejected the build. An inherited
`GRADLE_OPTS=-Dorg.gradle.dependency.verification=off` bypassed only the
project property and allowed that tampered build, while the gate-owned strict
`GRADLE_OPTS` restored rejection. The corresponding
`ORG_GRADLE_PROJECT_...=off` environment override did not supersede the
committed strict project property. The gate nevertheless removes both forms
instead of relying on their current precedence.

The focused gate contract passed 105/105 tests:

```bash
python3 -B -m unittest \
  tool.ci.tests.test_android_gate_common \
  tool.ci.tests.test_android_reference_app_gate \
  tool.ci.tests.test_android_sherpa_reference_app_gate
```

The graph-specific AAPT2 executable recorded by both metadata files is the
macOS artifact. Linux or Windows needs separately generated, reviewed
metadata and target-host evidence. This checkpoint verifies the Gradle/Maven
inputs of only these two graphs. The exact same-host AAB observation above is
not a general APK/AAB reproducibility claim. This checkpoint provides no
signing or distribution approval, Dart hosted-cache authentication, API 24 or
physical-device execution, installed AAB-derived split, performance, or QNN
evidence. It also does not retroactively bind the historical Android package
and runtime receipts below to strict dependency verification.

## Android application evidence

The final standalone gate used Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` (Flutter 3.47.0-0.1.pre),
command-line tools 20.0, build-tools 36.0.0, NDK 28.2.13676358, OpenJDK
21.0.12, and bundletool 1.18.3. Flutter was revision-pinned; the Android and
Java toolchains were path/version-gated; bundletool and the ORT archive and
payload were size/SHA-256 pinned. Both offline `pub get` passes enforced and
preserved the copied lockfile, and the generated `local.properties` matched the
supplied Android SDK. The bundletool jar was 32,520,401 bytes with SHA-256
`a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29`.

The source archive was 135,152,698 bytes with SHA-256
`9359e46eba4482ded00e678c98f22b68f51bb411d7934f5516d64050edfa3383`.
The closed packaged model inventory was:

- `mul_1.onnx`: 130 bytes, SHA-256
  `71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10`;
- `model.json`: 687 bytes, SHA-256
  `20ab7b1150a37516159c714abca3cb1cb6e48692da0c77f21c46e15336f71449`;
- `xnnpack_matmul.onnx`: 311 bytes, SHA-256
  `c75aaa93b0e1ae09e0bb12ddee5786c2fda803dfa5f565234e2b65e238623482`;
  and
- `xnnpack_matmul.json`: 1,298 bytes, SHA-256
  `76eb202b02211f34ee64ca6a88a2a24d9f58f334136fa7f1b7fcfe2e763f30ab`.

The packaged notice SHA-256 was
`fb0af774b4d7cffc5b9d046f2aaeade2f37df2f80abf8033c95dfffcc77a8866`.

Independent CPU and XNNPACK profile builds produced these audited arm64-v8a
artifacts:

- CPU APK: 42,767,029 bytes, SHA-256
  `47c190fe3d80832a7ba4a5847cee9dff6d2a2809a8dbac133d485b3b91009e0b`;
- CPU AAB: 25,594,073 bytes, SHA-256
  `dc404c3d5986aff503fa8ae4b988f0c42e14e8f0a12460b694abb3a2f068fc4b`;
- XNNPACK APK: 42,767,029 bytes, SHA-256
  `58103e45e493a416e570fe604cbcc631af4714ef7b47904e3d4921acf4b8e395`;
  and
- XNNPACK AAB: 25,594,265 bytes, SHA-256
  `15504fcbf4dcce1a9d21e0a7be1ca8933735923244792422d0dcdcbb8dc7a964`.

All four artifacts used the matching development-signing certificate SHA-256
`37b0508e9b25075e5c2831fb02927010cf19efccd204e018950aae4205aca1b3`,
packaged shim SHA-256
`054ec77ab4aa2520c77645c89a4dfd06f29f9878f6cbaa7a094bfb6d49007212`,
and packaged stripped ORT SHA-256
`dd5d6aa51f32f26c0c77d4a4cd5b62f5de80072369bf32a946a32608ee3f5774`.
The ORT loaded segments matched the 27,983,536-byte lock-selected payload with
SHA-256
`a7579e85ecc5465840d352c35f355e5b7418d36901670d36afd46555304458c2`.

Each artifact contained exactly four arm64 native libraries and the four
closed model/manifest assets, and passed the manifest, dependency, export,
notice, one-ORT, and static 16 KiB ELF checks. Both APKs passed
`zipalign -P 16`; both base-only AABs contained 83 archive entries, with 81
signed entries under the exact expected JAR-signature metadata inventory.

Each gate installed its exact audited APK on the named API 35 arm64 emulator,
verified a queried 4096-byte page size and installed `base.apk` hash equality,
and bound the structured receipt to the package UID. The CPU profile retained
its byte-for-byte receipt: ORT 1.27.1, application-owned bundled CPU, active
provider `cpu`, full assignment, output `[1,4,9,16,25,36]`, exact identities,
and double close.

The XNNPACK profile reported the same runtime identity and canonical
`intra_op_num_threads=1` configuration. Its strict CPU reference produced
`[7,10,15,22,23,34]`; six MatMul runs across two XNNPACK sessions each reported
one node assigned to `xnnpack` with exact CPU parity. The `mul_1` fallback
session reported one CPU node; the strict fallback session raised the closed
provider-evidence rejection with no output; a second XNNPACK cycle proved
recovery. The receipt bound five total sessions, idempotent double close, and
removal of every private profile root.

After each run, the app was force-stopped and uninstalled with verified
absence, and the gate verified that its owned emulator serial disappeared.
These are final-package functional emulator results only, not API 24, 16 KiB
runtime, physical-device, performance, thermal, AAB-split-install, x86_64,
sherpa-coexistence, QNN, or XNNPACK provider-qualification evidence.

## Android sherpa 4 KiB and 16 KiB target evidence

The bounded qualification path landed in `635e97d`; implementation commit
`8a9b6812c17237aeab7ec6d933f23932668c4b33` then corrected the source
inventory and made the committed-tree evidence reproducible. The source-final
`MANIFEST.sha256` is 36,887 bytes with SHA-256
`3728b31a38c246328cbeda7af14a7ba502489f6e89046371d2c22d29f264efd2`.
The runtime-provisioned gate copied the scaffold outside the checkout,
resolved the exact lock offline, verified all five hosted sherpa package trees
and generated four-plugin Android graph, passed 50 staged Flutter tests plus
analysis, and produced this arm64-v8a R8 Release package pair:

- APK: 45,070,602 bytes, SHA-256
  `35cca3502504b07d2d21fd27cb46fd5833fab9c6337115adfd5a309818a28ac3`;
- base-only AAB: 26,045,592 bytes, SHA-256
  `ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`.

The gate audited all eight qualification assets, the exact raw ORT, sherpa C
API, sherpa C++ API, and Fonix shim bytes, matching `libapp.so` and
`libflutter.so` loaded identities, the complete non-system dependency graph,
and static 16 KiB ELF compatibility. Its retained bindings are:

- 16,118-byte static package manifest:
  `99ccfb2f88eb16b9a7fdd3e03edf39c1529f771dedf210f5e481d85b3afd60e9`;
- 25,308-byte persisted static report:
  `6ad082a3cdf3a498f88504e24a47eb6e239a0d62f13ea02e11b7fbe202e4764e`;
- 1,552-byte target harness contract:
  `5460d723423515d519819e6e369646e6f6cd70edb89760c02da133da40cb7f23`;
- staged `pubspec.lock`:
  `c51a95db82b025011be6af9b93d02524bd10aae0cd9f051a1a86ed0b47e5ff76`.

The exact APK was installed independently for both load orders on each of two
API 35 arm64-v8a emulators. The 4 KiB target reported model token
`Android-SDK-built-for-arm64`, a 4096-byte page size, and fingerprint SHA-256
`acb4e14882d5e2e5cd4b91925de599cdc88a39b95d37a63ebe48c15f3000f384`.
The 16 KiB target reported model token `sdk_gphone16k_arm64`, a 16384-byte page
size, and fingerprint SHA-256
`d4cb1bb60eaee567df547e52dfdbbd5a1898d186aa9aa64f09c9b62a962f01ee`.

All four captures reported sherpa-owned process runtime resolution, ONNX
Runtime 1.27.0, and negotiated API 27. Each completed two strict alternating
cycles, accepted and settled native Fonix cancellation, retired the bounded
sherpa detector after between-frame cancellation, suppressed the induced stale
completion, recovered both engines, passed both disposal orders and double
disposal, and settled with zero pending work. The package was force-stopped,
uninstalled, and verified absent after every run.

The retained evidence hashes are:

| Page size | Load order | Trusted capture manifest | Raw receipt | Validation record |
| --- | --- | --- | --- | --- |
| 4 KiB | `dart-first` | `d5cf010cb70782bb9d542b0d3b68e8272b676d127f3c72679ad24c21ebb40821` | `bfc81d2e02e88c0223687cde15bb0b1df91bb2feea37c912bd729092ffc29bce` | `8c9da03a622cd6e97883396d6a9a8ff9ca5ee01c0ec477aaff12f6caf319c4ff` |
| 4 KiB | `sherpa-first` | `6ab621e2f11ed7e3ce3b08b1bb60c1b228dc5f6385f6cabdf1685474fdb06abe` | `224cded3e1070074b98e3ee8b9c3b1296699e7e3606b0bdc70bc290cfbadf2ef` | `fc0f89c630b4ace1517e59db70fce6771b57a65a5040e151624666a01c4d501a` |
| 16 KiB | `dart-first` | `56f13fa49e079c60a3deec3da4ed2068a3abe7a9409fe23d099dd87fa0b14e9c` | `0ecd917d2d0fe2b1e93e570dd5c802878d4f638c7f64e61f09b5eb0345d91067` | `82b811ba6f7f51e3793a76b4816e1d41e909478c3c41e21a33c8f6088f2b855f` |
| 16 KiB | `sherpa-first` | `2c3371ce831eaae566c8abda5a6da9fe46ee857a23b919a5c69a4304ed646764` | `145f1d98440b896adfda283e5a4dde8a943ac2fda3de5b5210ba9268e943147d` | `e7255259522aec2c1fd8342ea68e1796d9a961f339127fdfc394d371cb91aa63` |

Each capture manifest records `trusted-adb-capture`. The four validation
records and the aggregate compatibility manifest remain
`offline-consistency-only`; the latter has SHA-256
`0eb7fece2610b0225060696aa8599a960d01955cc1a7bc6f2ba8d7596964f504`.
This evidence is limited to the exact API 35 emulator, arm64-v8a, Release APK,
and named 4 KiB/16 KiB tuples. It does not prove API 24 execution, a physical
device, an AAB-derived installed split, x86_64, performance, thermal behavior,
signing, distribution, or another tuple. The next target-evidence step is the
exact Linux x86_64 target-host run, while lifecycle/resource stabilization and
scoped pre-1.0 release engineering can proceed locally in parallel for only
exact advertised tuples. Android QNN and all Windows target-host, provider,
final-package, installer, and clean-machine work remain deferred.

## Required CI coverage

The checked workflow requires minimum/stable Dart format-analysis-tests,
isolated binding regeneration, both Python verifier collections, the C source
gate, release/source evidence, deterministic fixture bytes on Linux/macOS/
Windows, Linux/macOS Address/UndefinedBehavior sanitizer jobs, the separate
Linux x64 ThreadSanitizer job, the Windows shim/security
contract, exact macOS ORT native/Dart/worker/provider tests, and the fresh
macOS final-application gate. The exact macOS lane now runs both production
native-asset bindings and the explicit dynamic-adapter seam against the same
strict fake runtimes. It also runs the committed reference application from a
clean external copy after the independent bundle audit.

Android auditor/orchestration/target-runner tamper tests run in the Python CI
collection. The standalone Android Gradle package gates and CPU/XNNPACK
emulator receipts, plus the source-final sherpa static gate, all four trusted
4 KiB/16 KiB target captures, and the aggregate compatibility manifest, were
run locally with explicitly provisioned revision-pinned, byte-pinned, and
version-gated inputs described above. They are not yet hosted CI lanes or
reproducible release builds. The new graph-specific strict SHA-256 metadata,
deterministic regeneration, and separately provisioned offline Gradle replays
close the former metadata-absence gap but do not retroactively change those
historical package/runtime records or establish binary reproducibility.

The Apple auditor and iOS reference-gate unit/tamper suites also run in the
ordinary Python collection. The full iOS build/audit/simulator gate is locally
provisioned evidence rather than a hosted CI lane. Its exact manifest
`9c790a572f9876262496ffc47f85e2cbc4c027a9e3846cdd43c5d5489a14f23a`
and source tree
`2c275c655d3569ec767f36d9e99522b5e38ba937e6236666abe0795ea43a03a2`,
archive, toolchain, unsigned device `.app`, audited simulator application,
transport-normalized installed tree, launch binding, receipt, settlement, and
uninstall are one closed tuple; none may be inferred from source tests alone.

## Evidence not produced, scoped exclusions, and release blockers

The following are deliberately not claimed:

- physical iOS device execution, lifecycle, provisioning, approved device or
  distribution signing, IPA/App Store packaging, or distribution evidence;
- iOS CoreML/XNNPACK/GPU/Neural Engine assignment, representative workloads,
  sustained performance, thermal behavior, or provider qualification;
- iOS simulator Release execution or transfer of the exact Debug simulator
  result to another tuple;
- Android API 24 execution, physical-device or x86_64 inference, an installed
  AAB-derived split, or transfer of the exact 4 KiB/16 KiB emulator results to
  another tuple;
- physical-device XNNPACK assignment/parity, representative workloads,
  sustained performance, thermal behavior, and benchmark-backed provider
  qualification;
- deferred Android QNN aligned-build/device/HTP/cache/product receipts;
- Linux target-host loading/inference and final Flutter packaging;
- deferred Windows target-host loading/inference, final Flutter packaging,
  installer, and clean-machine runs;
- physical-device or representative-hardware qualification and sustained
  benchmarks for every other non-CPU provider; or
- notarization, public signing, publication, or redistribution approval.

For the planned scoped pre-1.0 release, a missing gate blocks publication only
when its exact target/provider tuple is selected for advertising. Deferred
Android QNN, every deferred Windows target-host/provider/package/install path,
and other unadvertised tuples must remain explicitly unsupported, but they are
not completion gates for that narrower release. They remain gates for
five-platform, unqualified `Release-ready`, and 1.0 claims. Existing portable,
static, source, tamper, cross-build, and loader-security regressions remain
mandatory even for excluded tuples.

The scoped candidate validator does not perform cryptographic signature
verification. It binds every evidence file by exact ID, media type, size, and
SHA-256 and binds the candidate statement to an out-of-band bundle SHA-256.
The source archive additionally undergoes bounded no-extraction semantic
inspection through that same retained descriptor, and the shared source-closure
record must exactly match the canonical derived result. This proves offline
archive/repository consistency, not archive origin or provenance. External
verification receipts, their authority, and the actual detached-signature
verification remain external release inputs. Until the complete external
bundle passes with `--require-scoped-ready`, scoped publication remains blocked.

In particular, the reference app's local ad-hoc build disables library
validation because it has no authorized Team ID shared with FlutterMacOS. That
explicit development entitlement is executable gate evidence, not a
distribution security/signing decision.

The repository also has no project-level `LICENSE`, approved private security
contact, signing identity, or publication authority. Those are product/legal/
release decisions and cannot be inferred from passing source tests or from the
vendored ONNX Runtime license and notices. They remain the blockers to changing
`publish_to: none` or promoting any unqualified support row.
