# Fonix + sherpa Android reference harness

This is the dedicated arm64-v8a Flutter application scaffold for the first
Android sherpa-owned coexistence slice. It is intentionally separate from the
standalone Fonix example because this application resolves Fonix through the
single ONNX Runtime packaged by sherpa-onnx.

## Current evidence status

This checkpoint includes the real bounded qualification path and an exact
runtime-provisioned arm64-v8a R8 Release APK/base-only-AAB static package audit.
The implementation landed in commit `635e97d` and its staged build was
stabilized in `eaa195a`. It contains no target execution evidence yet.

The passing gate ran on macOS arm64 with Flutter revision
`bd1e75d918605c91b411e8789fb911e6c9a84534` and OpenJDK 21.0.12. It produced:

- a 42,979,622-byte APK with SHA-256
  `d6032230cebfa6fb2ab89be170579b4b1b848adb0c0485b1ac084e9e9e8a46e8`;
- a 26,045,591-byte base-only AAB with SHA-256
  `fe39a7870067e1b0b5691ba9f1ce94fa48b9bfd8640b0125410d27bdecc2aa3e`;
- a 16,118-byte static manifest with SHA-256
  `8be28263a802f757cd66b42f39234111bc0d99aeb3df8cd069cdb2b4eff34765`;
  and
- a gate report with SHA-256
  `17dbf5c2cc1cd2d76300034721ad49ea41bb5c8cad236ef15f89b3b00026e6fa`.

The gate verified the `eaa195a` implementation tree's `MANIFEST.sha256`, whose
SHA-256 was
`3cabcad849ca2480a0b7d6e9bff17b176f295ac4369ebf7d7057e5c1934f1f8b`
and passed 50 staged Flutter tests plus analysis. An API 35 arm64 emulator was
queried with a 4096-byte page size, but installation, launch, and target-runner
execution were not authorized and did not occur. That query is not target
evidence.

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
  uninstall, schema-2 receipt assembly, and offline validation. A passing
  runner test is still not a target run.

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
  --sherpa-model /absolute/path/to/silero_vad.int8.onnx
```

The model is an external build input and must match the gate's exact size and
SHA-256. It is copied only into the new staged build and must not be added to
this repository.

The repository gate builds and statically audits both outputs. Passing
`--sherpa-model` opts into the exact runtime fixture set; omitting it preserves
the asset-free unavailable template. The separate trusted target runner
installs one audited APK on one selected adb serial, captures one load order and
queried page size, validates it, then force-stops and removes only the package
installed by that invocation. Until that runner succeeds on a target, this
source still carries no runtime coexistence claim.

The next target steps are both `dart-first` and `sherpa-first` against the exact
audited APK on the available API 35 arm64/4 KiB emulator, followed by both load
orders on a real 16 KiB Android environment. The repository roadmap then moves
to iOS arm64 and Linux x86_64 target-host work. Android aligned-build/QNN
target qualification and all Windows target-host, provider/DirectML,
final-application/package, installer, and clean-machine qualification remain
deferred; existing static, source, cross-build, and security checks remain
mandatory.
