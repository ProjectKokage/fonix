# Fonix macOS reference application

This committed Flutter application demonstrates Fonix through
`package:fonix/fonix.dart` only. It loads the bundled, lock-selected ONNX
Runtime, creates one worker-isolate session, runs the deterministic CPU smoke
model, exposes cancellation and retry, and reports path-free runtime and
per-run assignment evidence.

The app is a development reference for macOS arm64 at a 14.0 deployment floor.
It is not a release artifact. The included 130-byte model is an API and
packaging smoke fixture, not a representative performance workload.

The Release target keeps the app sandbox and hardened runtime enabled but
declares `com.apple.security.cs.disable-library-validation`: the local gate has
no authorized Apple signing identity, so the app and Flutter framework are
ad-hoc signed with no common Team ID. A distribution build must use its
approved consistent signing identity and separately review or remove this
development entitlement; this gate proves neither distribution signing nor
notarization.

## Run the complete gate

The committed `example/` directory is a source template. Do not build it in
place: a consuming app's native-asset output must be outside the Fonix package
checkout, while this template is intentionally nested inside that checkout.
The gate makes a bounded clean copy at a new absolute path, patches only the
local Fonix dependency, and keeps every generated native byte outside source.

Provision the exact lock-selected archive and reference dylib, then run:

```sh
python3 -B tool/ci/run_macos_reference_app_gate.py \
  --repository /absolute/path/to/fonix \
  --flutter /absolute/flutter/bin/flutter \
  --artifact-cache /absolute/verified/cache \
  --reference-runtime /absolute/verified/libonnxruntime.1.27.1.dylib \
  --work-dir /absolute/new/fonix-reference-gate
```

`--work-dir` must be an absolute path that does not yet exist. Use Flutter
revision `bd1e75d918605c91b411e8789fb911e6c9a84534`. The gate works offline
after provisioning: it verifies and copies the exact archive, reproduces the
committed manifest/notices, resolves packages offline, analyzes and tests the
app, builds Release, independently audits the final application, and then
launches the packaged executable in bounded one-shot mode.

After a successful gate, the copied app remains at `--work-dir`. To inspect the
interactive UI with the already resolved inputs:

```sh
cd /absolute/new/fonix-reference-gate
flutter run -d macos --no-pub
```

## Maintain the committed native assets

When the lock-selected macOS artifact changes, place the exact archive in the
ignored template cache and regenerate the two app-owned source assets:

```sh
cd /absolute/path/to/fonix/example
mkdir -p .fonix-artifact-cache
cp /absolute/verified/cache/onnxruntime-osx-arm64-1.27.1.tgz \
  .fonix-artifact-cache/
flutter pub get --offline
dart run fonix:fonix_prepare_flutter_assets \
  --target-os macos \
  --architecture arm64 \
  --variant default \
  --package-root /absolute/path/to/fonix \
  --cache /absolute/path/to/fonix/example/.fonix-artifact-cache \
  --output /absolute/path/to/fonix/example/assets/fonix
```

The preparation command must reproduce the two committed app-owned files in
`assets/fonix/` exactly. It does not download. Run the complete external-copy
gate afterward because publication is a two-file operation and Flutter
packaging happens later.

The application starts its worker lazily, keeps at most one run outstanding,
invalidates stale completions before cancellation or shutdown, and recreates
the worker after suspension or a terminal failure. Closing is graceful: it is
not a hard deadline when an execution provider does not cooperate with ONNX
Runtime termination.

CI additionally builds the Release `.app`, runs the independent Mach-O,
signature, native-identity, notice, and packaged C probe audit, and then
launches a bounded one-shot smoke mode from the packaged executable. It checks
the exact numeric output and CPU assignment receipt and closes the worker twice
to prove idempotence.

The widget/controller tests use an app-owned fake backend. They do not load
native code and therefore prove application state ownership, cancellation,
retry, terminal-backend replacement, stale-result suppression,
suspend/resume oscillation, synchronous-listener reentrancy, and disposal
behavior—not native cancellation latency or another platform/provider claim.
