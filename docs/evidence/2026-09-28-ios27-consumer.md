# iOS 27 consumer shim qualification

The explicit `consumer-ios27` Apple audit profile passes for the SDK 27.0
linked arm64 shim. The existing `reference` and `consumer` profiles retain
their SDK 26.5 metadata. This adds an audit profile, not a new runtime, ABI,
deployment floor, execution provider, or distribution approval.

The host was macOS 27.0 (26A428), Xcode 27.0 (27A266a), iPhoneOS and
iPhoneSimulator SDK 27.0, LD 27037.1, Flutter 3.47.2 / Dart 3.13.2. Native inputs
remain the checksum-pinned ORT 1.27.1 archives from `native/versions.lock.yaml`.
The build-hook dependency update is `182c1c9`; the qualification uses the
auditor and reference-app fixes accompanying this record. The final consumer
rebuild and Simulator receipt include the portable zero-initialization fix from
`3301822`; subsequent changes repair CI setup and test fixtures.

## Exact metadata change

Both device and Simulator shims contain these dependencies in this order:

| Dependency | Current version | Compatibility version |
| --- | --- | --- |
| Foundation | 5027.0.69 | 300.0.0 |
| CoreML | 1.0.0 | 1.0.0 |
| libc++.1 | 2200.27.0 | 1.0.0 |
| libSystem.B | 1359.0.0 | 1.0.0 |
| CoreFoundation | 5027.0.69 | 150.0.0 |
| libobjc.A | 228.0.0 | 1.0.0 |

All six use ordinary load commands. Their canonical metadata SHA-256 is
`9add32b23d4d1e7ad5fff4b9ebe35deadb6a3dd7b4bd684de17987e4ae55ac1b`.
The new profile also requires exactly one build-version command with the
selected device/Simulator platform, minimum iOS 15.1, SDK 27.0, and LD 27037.1.
All existing dependency-path, load-command-kind, header, install-ID, RPATH,
export, signature, archive identity, hook provenance, and normalized runtime
comparisons remain mandatory. Unknown versions are rejected.

## Executed qualification

- Kokage `a8f2ebab`, unsigned arm64 Release `1.0.0+34`, application floor 18.6:
  complete consumer audit passes with `ios-device-unsigned-development`.
  Its claim remains **static-only**. Framework privacy inspection also passed.
- External copy of the committed Fonix example, Debug arm64 Simulator,
  application floor 15.1: complete strict consumer audit passes. Xcode 27's
  additional `@loader_path` is removed by the Debug-only normalization phase;
  the final exact two-RPATH requirement is unchanged.
- iPhone 18 Pro Simulator, iOS 27.0 (24A434): installed directory paths, bytes,
  and executable identity match the audited package with the existing closed
  install-transport normalization. A fresh challenge and PID bind the CPU
  receipt: linked wrapper-owned ORT 1.27.1, generated model
  `71f431c4e9321ec6fbeb158d02ed240459a7dcc98673fa79a4f439ce42efaf10`, exact
  output `[1,4,9,16,25,36]`, full CPU assignment, and double close. Process
  settlement, test-app uninstall, and shutdown of the task-booted Simulator
  completed.
- The older gate's attempts to spawn macOS `sysctl`, `arch`, and `ps` fail on
  this host. This run instead compiled an arm64 **IOSSIMULATOR** diagnostic
  with warnings as errors. It executes `sysctlbyname("hw.optional.arm64")`,
  `uname`, and `sysctl(KERN_PROC_PID)` inside the Simulator to retain the
  capability, executing-architecture, and live/dead PID checks. No architecture
  or process-settlement check was skipped. This is a scoped qualification,
  not a pass of the unchanged, older-toolchain reference gate.
- All 28 host native CTests pass with ASan/UBSan (AppleClang has no LSan).
  The runner now selects only its owning `fonix_shim_build` or `bundle` output
  directory, according to the suite;
  deliberate copies made by hostile-path/relocation tests no longer make the
  final output lookup ambiguous. Multiple target outputs still fail.
- Verifier regressions cover SDK/linker/platform/floor/dependency mutations,
  preservation of the historical profile, unknown or duplicated RPATHs, and
  missing or ambiguous native target outputs.

Retained artifacts are in `/private/tmp/kokage-voice-performance/`:

| Artifact | SHA-256 |
| --- | --- |
| `final-ios-audit.json` | `84be0a8ba5719d1e9d405ff62f327bafaec838928fc29e78cca91c575a46cae0` |
| `ios27-simulator-audit.json` | `b90d1fc1657d5096315b5f3af05ec738b3b5aecc6938d793212d5519d1ca909a` |
| Installed Simulator tree | `70f649414e3ebc4fced82b59e6b15c3ab1db650726a8a64b077d6f690a3929cc` |

The directory also retains the CPU receipt, generated diagnostic source/binary,
qualification driver, full native and verifier logs, and the external example.
To audit a new consumer, use the ordinary final-app command from the packaging
guide with `--ios-application-profile consumer-ios27` and its own exact
`--reference-shim`. Device development packages additionally require the
explicit unsigned-development signature policy; its result is never signed
release evidence.

Physical iPhone/iPad execution, Distribution signing, archive multiplicity,
other-Mach-O static ORT copies, runtime `dlopen`, CoreML/XNNPACK, performance,
and distribution remain outside this result.
