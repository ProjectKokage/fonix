# Fonix + sherpa Android reference harness

This is the dedicated arm64-v8a Flutter application scaffold for the first
Android sherpa-owned coexistence slice. It is intentionally separate from the
standalone Fonix example because this application resolves Fonix through the
single ONNX Runtime packaged by sherpa-onnx.

## Current evidence status

The source-final checkpoint includes an exact arm64-v8a R8 Release
APK/base-only-AAB static package audit and the complete trusted four-record
matrix: `dart-first` and `sherpa-first` captures on API 35 arm64-v8a/4 KiB and
16 KiB emulators. Implementation commit
`8a9b6812c17237aeab7ec6d933f23932668c4b33` corrected the source inventory
and made the committed-tree evidence reproducible. This remains exact-tuple
evidence rather than redistribution, release approval, or support promotion.

The passing gate ran on macOS arm64 with Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` and OpenJDK 21.0.12. It bound the
36,887-byte source `MANIFEST.sha256` file with SHA-256
`3728b31a38c246328cbeda7af14a7ba502489f6e89046371d2c22d29f264efd2`
and produced:

- a 45,070,602-byte APK with SHA-256
  `35cca3502504b07d2d21fd27cb46fd5833fab9c6337115adfd5a309818a28ac3`;
- a 26,045,592-byte base-only AAB with SHA-256
  `ce4cda9022a27c731522cfa75a8cb5bfb90d8a42d671ef0e608b5b1567b3396c`;
- a 16,118-byte static manifest with SHA-256
  `99ccfb2f88eb16b9a7fdd3e03edf39c1529f771dedf210f5e481d85b3afd60e9`;
- a 25,308-byte persisted gate report with SHA-256
  `6ad082a3cdf3a498f88504e24a47eb6e239a0d62f13ea02e11b7fbe202e4764e`;
- a 1,552-byte harness contract with SHA-256
  `5460d723423515d519819e6e369646e6f6cd70edb89760c02da133da40cb7f23`;
  and
- a staged `pubspec.lock` with SHA-256
  `c51a95db82b025011be6af9b93d02524bd10aae0cd9f051a1a86ed0b47e5ff76`.

The exact APK was installed independently for both load orders on each API 35
arm64-v8a emulator. The 4 KiB target reported model token
`Android-SDK-built-for-arm64`, a 4096-byte page size, and fingerprint SHA-256
`acb4e14882d5e2e5cd4b91925de599cdc88a39b95d37a63ebe48c15f3000f384`.
The 16 KiB target reported model token `sdk_gphone16k_arm64`, a 16384-byte page
size, and fingerprint SHA-256
`d4cb1bb60eaee567df547e52dfdbbd5a1898d186aa9aa64f09c9b62a962f01ee`.
All four runs reported sherpa-owned process mode, ONNX Runtime 1.27.0, and API
27, and passed two alternating cycles plus the complete cancellation, stale,
recovery, disposal, and zero-pending-work contract. The package was
force-stopped, uninstalled, and verified absent after every run.

| Page size | Load order | Trusted capture manifest | Raw receipt | Validation record |
| --- | --- | --- | --- | --- |
| 4 KiB | `dart-first` | `d5cf010cb70782bb9d542b0d3b68e8272b676d127f3c72679ad24c21ebb40821` | `bfc81d2e02e88c0223687cde15bb0b1df91bb2feea37c912bd729092ffc29bce` | `8c9da03a622cd6e97883396d6a9a8ff9ca5ee01c0ec477aaff12f6caf319c4ff` |
| 4 KiB | `sherpa-first` | `6ab621e2f11ed7e3ce3b08b1bb60c1b228dc5f6385f6cabdf1685474fdb06abe` | `224cded3e1070074b98e3ee8b9c3b1296699e7e3606b0bdc70bc290cfbadf2ef` | `fc0f89c630b4ace1517e59db70fce6771b57a65a5040e151624666a01c4d501a` |
| 16 KiB | `dart-first` | `56f13fa49e079c60a3deec3da4ed2068a3abe7a9409fe23d099dd87fa0b14e9c` | `0ecd917d2d0fe2b1e93e570dd5c802878d4f638c7f64e61f09b5eb0345d91067` | `82b811ba6f7f51e3793a76b4816e1d41e909478c3c41e21a33c8f6088f2b855f` |
| 16 KiB | `sherpa-first` | `2c3371ce831eaae566c8abda5a6da9fe46ee857a23b919a5c69a4304ed646764` | `145f1d98440b896adfda283e5a4dde8a943ac2fda3de5b5210ba9268e943147d` | `e7255259522aec2c1fd8342ea68e1796d9a961f339127fdfc394d371cb91aa63` |

All capture manifests claim `trusted-adb-capture`. The validator records and
aggregate compatibility manifest remain `offline-consistency-only`; the
compatibility manifest has SHA-256
`0eb7fece2610b0225060696aa8599a960d01955cc1a7bc6f2ba8d7596964f504`.
This does not prove API 24, a physical device, x86_64, an installed AAB-derived
split, performance, thermal behavior, signing, distribution, or another tuple.

- The committed template declares no qualification assets. `main.dart`
  validates the trusted-runner launch envelope and emits
  `unavailable/native-fixtures-unprovisioned` only when the fixed marker is
  absent from Flutter's asset index.
- The opt-in staged build generates the exact Fonix model/input/reference and
  synthetic VAD audio/reference bytes, accepts one exact externally
  provisioned Silero model, and audits all eight assets in both the APK and
  base AAB. No model weight, recording, microphone path, or network permission
  is committed to the template.
- The ready path uses real process-runtime `OrtIsolateSession` and
  `sherpa_onnx` VAD adapters. They hash or validate the bytes they consume,
  report observed runtime/fixture/profile identities, and use bounded native
  owners with idempotent cleanup.
- Current, cancellation, and retired Fonix completions traverse one bounded
  authoritative publication sink. The state machine requires accepted active
  native termination, suppresses the induced stale completion, recovers both
  engines, and settles both disposal orders with no pending work.
- Host tests retain injected seams. They prove orchestration, strict decoding,
  bounds, publication ownership, failure reduction, and cleanup logic; they
  are not Android, ONNX Runtime, Fonix, sherpa, VAD, APK, or device evidence.
- The trusted one-tuple target runner owns installation, a fresh challenge,
  queried device/page-size facts, UID/PID-bound log framing, force-stop,
  uninstall, schema-2 receipt assembly, and offline validation. Its four
  retained 4 KiB/16 KiB captures above are target runs; a passing runner unit
  test alone is not.

## Locked composition

`pubspec.yaml` and `pubspec.lock` select:

- Fonix `0.1.0-dev.1` from `../../..`;
- `sherpa_onnx` `1.13.4`;
- every federated sherpa platform package overridden to exactly `1.13.4`;
- `android_runtime_owner: sherpa`; and
- `runtime_mode: external`.

The Fonix hook therefore emits only the process-resolving shim. It must not
package `libonnxruntime.so`. There is no artifact cache or mirror in this
composition and no Gradle `pickFirst`.

The Android project is closed to:

- application ID and namespace `dev.fonix.sherpa_reference`;
- `arm64-v8a` only;
- API 24 minimum, API 36 target/compile;
- NDK `28.2.13676358`;
- Java/Kotlin 17;
- AGP `9.1.0`, Kotlin `2.4.0`, and Gradle `9.3.1`; and
- non-debuggable, non-JNI-debuggable, R8-minified, resource-shrunk Release.

Release packaging keeps debug-symbol stripping disabled for the four exact
provenance-bound native inputs. The default Android Release strip step rewrites
the selected ORT ELF header inside its first load segment, so allowing that
transformation would make a source-to-final byte binding impossible. This does
not make the application debuggable or relax R8/resource shrinking.

Release currently uses the debug signing key only for local qualification. It
is not a distribution identity.

## Trusted host launch contract

The host must force-stop any old process, create a fresh bounded binary
challenge, base64-encode it with standard padded base64 and no whitespace, and
launch `.MainActivity` with exactly one closed load order:

```text
dev.fonix.sherpa_reference.LOAD_ORDER
  dart-first | sherpa-first

dev.fonix.sherpa_reference.LAUNCH_CHALLENGE_BASE64
  canonical standard base64 for 1..1024 bytes
```

For example, after safely producing `CHALLENGE_BASE64` outside this example:

```bash
adb shell am start \
  -n dev.fonix.sherpa_reference/.MainActivity \
  --es dev.fonix.sherpa_reference.LOAD_ORDER dart-first \
  --es dev.fonix.sherpa_reference.LAUNCH_CHALLENGE_BASE64 "$CHALLENGE_BASE64"
```

Do not place a secret in the challenge. It is a correlation nonce and its
SHA-256 appears in the app result.

The MethodChannel is
`dev.fonix.sherpa_reference/harness`. Dart first invokes `readLaunch` with
no arguments. Android returns exactly:

```json
{
  "schemaVersion": 1,
  "loadOrder": "dart-first",
  "launchChallengeBase64": "<canonical-standard-base64>"
}
```

The Android boundary rejects an absent, extra, mistyped, non-canonical, empty,
or oversized launch value.

## App completion envelope

Dart invokes `complete` once with exactly:

```json
{
  "schemaVersion": 1,
  "status": "passed",
  "payloadSha256": "<lowercase-sha256-of-payloadJson-utf8>",
  "payloadJson": "<minified-printable-ASCII-JSON>"
}
```

`status` is closed to `passed`, `unavailable`, or `failed`. The payload
is at most 512 KiB. Android rehashes it, requires exact primitive JSON types,
binds its challenge hash and load order to the validated launch, and rejects a
second completion.

The asset-free committed template emits exactly this unavailable payload:

```json
{
  "schemaVersion": 1,
  "result": "unavailable",
  "launchChallengeSha256": "<sha256>",
  "loadOrder": "dart-first",
  "reason": "native-fixtures-unprovisioned"
}
```

An unexpected staged native qualification failure emits the same five keys
with `result: "failed"` and `reason: "qualification-failed"`. Neither status
is a passed receipt.

## Exact passed device-result shape

The app-owned passed payload contains only facts observed or computed inside
the launched application. Its top-level keys and order are:

```json
{
  "schemaVersion": 1,
  "result": "passed",
  "launchChallengeSha256": "<sha256>",
  "loadOrder": "dart-first",
  "runtime": {
    "runtimeOwner": "sherpa",
    "runtimeSource": "process",
    "ortVersion": "<observed-semver>",
    "requiredOrtApi": 27,
    "negotiatedOrtApi": 27,
    "shimAbi": 1,
    "shimBuildId": "android-owner-sherpa-source-process"
  },
  "sherpa": {
    "getVersion": "<value-returned-by-sherpa>",
    "getGitSha1": "<7-to-40-lowercase-hex-returned-by-sherpa>",
    "profile": {
      "id": "<closed-profile-token>",
      "provider": "cpu",
      "sampleRateHz": 16000,
      "windowSamples": 512,
      "numThreads": 1,
      "thresholdMillionths": 500000,
      "minimumSpeechMilliseconds": 250,
      "minimumSilenceMilliseconds": 800,
      "maximumSpeechMilliseconds": 30000,
      "bufferMilliseconds": 60000
    }
  },
  "fixtures": {
    "fonixModelSha256": "<sha256>",
    "fonixInputSha256": "<sha256>",
    "fonixReferenceOutputSha256": "<sha256>",
    "fonixCancellationModelSha256": "<sha256>",
    "fonixCancellationInputSha256": "<sha256>",
    "sherpaModelSha256": "<sha256>",
    "sherpaAudioSha256": "<sha256>",
    "sherpaReferenceSha256": "<sha256>"
  },
  "initialization": {
    "events": ["fonix-session-ready", "sherpa-vad-ready"],
    "firstOwnerAliveWhenSecondReady": true
  },
  "workload": {
    "requestedCycles": 2,
    "completedCycles": 2,
    "steps": [
      {
        "ordinal": 1,
        "engine": "fonix",
        "outputEncoding": "float32-le",
        "outputBytesBase64": "<canonical-base64>",
        "outputSha256": "<sha256>"
      },
      {
        "ordinal": 2,
        "engine": "sherpa",
        "sourceSamples": 1,
        "submittedSamples": 512,
        "segments": [{"startSample": 0, "sampleCount": 1}],
        "queueEmptyAfterDrain": true,
        "detectedAfterDrain": false
      }
    ]
  },
  "lifecycle": {
    "fonixCancellation": {
      "mode": "active-native-termination",
      "requestCount": 1,
      "nativeRequestAcceptedCount": 1,
      "settlementCount": 1,
      "cancelledResultCount": 1,
      "publishedOutputCount": 0,
      "outstandingRunsAfterSettlement": 0,
      "settledBeforeRecovery": true
    },
    "sherpaCancellation": {
      "mode": "between-bounded-frames",
      "requestCount": 1,
      "framesAcceptedBeforeRequest": 1,
      "framesAcceptedAfterRequest": 0,
      "flushCallsAfterRequest": 0,
      "segmentsPublishedAfterRequest": 0,
      "detectorRetiredBeforeRecovery": true
    },
    "staleCompletion": {
      "inducedCount": 1,
      "observedCount": 1,
      "suppressedCount": 1,
      "publishedOutputCount": 0,
      "retiredGeneration": 1,
      "authoritativeGeneration": 2
    },
    "recovery": {
      "fonixOutputEncoding": "float32-le",
      "fonixOutputBytesBase64": "<canonical-base64>",
      "fonixOutputSha256": "<sha256>",
      "sherpa": {
        "sourceSamples": 1,
        "submittedSamples": 512,
        "segments": [{"startSample": 0, "sampleCount": 1}],
        "queueEmptyAfterDrain": true,
        "detectedAfterDrain": false
      }
    },
    "disposal": {
      "ordersTested": ["fonix-then-sherpa", "sherpa-then-fonix"],
      "fonixSessionsCreated": 2,
      "fonixSessionsClosed": 2,
      "sherpaDetectorsCreated": 3,
      "sherpaDetectorsFreed": 3,
      "fonixDoubleClose": "passed",
      "sherpaDoubleFree": "passed",
      "pendingFonixRuns": 0,
      "queuedSherpaSegments": 0,
      "temporaryRootsCreated": 5,
      "temporaryRootsRemoved": 5,
      "temporaryRootsRemaining": 0
    }
  }
}
```

The sample values above illustrate types, not fixture evidence. Real step
counts are exactly two per requested cycle, always Fonix then sherpa, for 2..64
cycles. Sherpa observations are checked against fixture-owned, bounded
invariants for source length, segment count, per-segment length, and total
detected samples; the scaffold does not require one synthetic exact segment
layout. Initialization event order follows the requested load order.

The state machine owns a sole lifecycle-output publisher. Every real and fake
Fonix run is bound to that sink before cancellation or generation retirement;
the sink consumes the run's sole settlement future and decides whether output
is current, cancelled, or stale and suppressed. A deliberately leaky fake must
fail. Host tests prove this orchestration contract, while only a trusted target
run can turn the real adapter's observed counts into device evidence.

The Fonix adapter maps
`OrtIsolateRun.cancelWithDisposition()` exactly into the harness's closed
cancellation disposition. Only
`OrtRunCancellationDisposition.nativeTerminationRequested` maps to
`nativeTerminationRequested`. `queuedRunRemoved` and `notCancelled` fail
this checkpoint even if the run later reports cancellation. The adapter must
then independently await the run result, require the typed cancelled result,
observe zero outstanding runs on the same owning session, and recover on that
same session. The disposition proves that registered run options accepted a
termination request; it does not by itself prove precisely when ORT entered
`Run`.

Initialization requires an adapter-owned native liveness probe of the first
owner after the second owner reports ready; a cached Dart boolean is not
sufficient. Driver factory construction is native-inert: it does not load a
library, allocate a native owner, or create a temporary root. `initialize()` is
the first native operation, and the requested first owner is initialized before
the second driver is constructed. The state machine rejects non-zero driver
state or changed native ownership counters immediately after construction.
Disposal counts come from monotonic native-adapter
created/closed/freed/root-removed counters and must settle exactly. The raw
schema name `sherpaDoubleFree` means that the app calls idempotent
`close()` twice while the adapter observes exactly one native `free()`; it
never authorizes two native frees. The first disposal order closes primary
Fonix before a still-live recovery sherpa detector, and the reverse order uses
a separate still-live pair.

## Logcat framing consumed by the host

`MainActivity` writes the accepted envelope under tag
`FonixSherpaRef`. It does not log one oversized JSON line. The framing is:

```text
FONIX_SHERPA_COMPLETION_BEGIN|1|<status>|<payload-sha256>|<ascii-bytes>|<chunks>
FONIX_SHERPA_COMPLETION_CHUNK|<payload-sha256>|0|<up-to-3000-ascii-characters>
FONIX_SHERPA_COMPLETION_CHUNK|<payload-sha256>|1|<next-chunk>
...
FONIX_SHERPA_COMPLETION_END|<payload-sha256>
```

Chunk indexes are zero-based, contiguous, and ordered. Because `payloadJson`
is printable ASCII, its character count equals its UTF-8 byte count. The
trusted runner must filter by the independently observed PID/UID and tag,
require one begin/end pair, reject missing/duplicate/out-of-order chunks,
concatenate only the chunk body, and recheck both length and SHA-256.

The Activity deliberately remains alive after completion. The host must observe
the same PID after the receipt and then force-stop it. The app never claims its
own PID or UID.

## Host-owned assembly boundary

The app result is not
`templates/android/load_order_receipt.schema.json`. A trusted adb runner must
independently add and validate all host/source facts:

- application ID and exact final APK SHA-256;
- harness-contract, `pubspec.lock`, target-evidence, and logcat-evidence
  hashes;
- ABI, Release mapping, and queried page size;
- physical/emulator kind, Android API, model token, and fingerprint hash;
- package-manager/logcat/receipt UID and every before/after PID observation;
- force-stop settlement;
- exact packaged `libonnxruntime.so` ELF hash;
- hosted sherpa package version/provenance and the full selected source
  revision; and
- trusted installation, adb, package-manager, and raw-log provenance.

The host translates app `sherpa.getVersion` to raw-receipt
`sherpa.nativeVersion`, translates `getGitSha1` to
`sherpa.nativeRevision`, and verifies that value against the full selected
source revision. It adds `runtime.ortSha256` only after auditing the installed
APK. It must reject, not trust, any app payload that tries to supply a
`matrix`, `build`, `device`, `process`, ORT ELF hash, package provenance,
or full source revision.

Only after that join may the host construct a raw schema-2 receipt and invoke
`tool/ci/validate_android_load_order_receipt.py`. That validator still emits
`offline-consistency-only` with unverified target provenance; retained
trusted-runner capture is an additional requirement.

## Development checks

`flutter analyze` runs directly after locked package resolution. Normal host
`flutter test` cannot use the committed Android-only
`android_runtime_owner: sherpa` setting. The repository gate therefore copies
this source outside the checkout, rewrites only the Fonix path to the exact
repository, temporarily selects Fonix `runtime_mode: external` without an
Android owner, and preserves the locked Fonix and sherpa dependencies. It runs
locked offline resolution, analysis, and the fake/contract tests there, then
restores the exact Android sherpa-owner hook before the Release builds. The
committed dependency and Android hook contract are never weakened in place.

Release build outputs are the standard:

```text
build/app/outputs/flutter-apk/app-release.apk
build/app/outputs/bundle/release/app-release.aab
```

Run the locked runtime-provisioned build in a new directory outside the
repository:

```bash
python3 -B tool/ci/run_android_sherpa_reference_app_gate.py \
  --flutter /absolute/path/to/flutter \
  --work-dir /absolute/new/path/fonix-sherpa-runtime \
  --android-sdk /absolute/path/to/android-sdk \
  --java-home /absolute/path/to/jdk-21.0.12 \
  --sherpa-model /absolute/path/to/silero_vad.int8.onnx \
  --report /absolute/new/path/fonix-sherpa-runtime-report.json
```

The model is an external build input and must match the gate's exact size and
SHA-256. It is copied only into the new staged build and must not be added to
this repository. The optional report path must be absolute and new, with an
existing non-symlink directory owned by the current user and not writable by
group or other users as its parent, and it must remain outside the source
repository. Use a dedicated mode-`0700` evidence directory rather than a
shared temporary directory. File publication requires a POSIX host with
directory-descriptor and hard-link support. The gate retains the verified
parent across the build, writes and verifies a private temporary inode, and
publishes only the complete canonical pretty-printed JSON report with mode
`0600`, after the complete gate and postflight checks pass. If the first inode
identity read fails, no report bytes are written and the gate leaves the
unverified zero-byte temporary path untouched instead of risking deletion of a
raced replacement. Without `--report`, the same JSON remains on stdout on
every supported gate host.

The repository gate builds and statically audits both outputs. Passing
`--sherpa-model` opts into the exact runtime fixture set; omitting it preserves
the asset-free unavailable template. The separate trusted target runner
installs one audited APK on one selected adb serial, captures one load order and
queried page size, validates it, then force-stops and removes only the package
installed by that invocation. It has succeeded for both load orders on the
exact 4 KiB and 16 KiB tuples recorded above; that evidence does not transfer
to another target.

The four-record Android matrix and compatibility aggregation are complete for
the exact named tuples. The roadmap now moves to iOS arm64 and then Linux
x86_64 target-host work. Android aligned-build/QNN target qualification and all
Windows target-host, provider/DirectML, final-application/package, installer,
and clean-machine qualification remain deferred; existing static, source,
cross-build, and security checks remain mandatory.
