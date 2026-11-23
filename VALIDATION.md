# Validation report

- Date: 2026-11-23
- Host: macOS 27.0.1 arm64, Xcode 27.2, Dart 3.14.0 (Flutter 3.50.0)
- Package: `fonix 0.1.0-dev.1` plus the unreleased move to ONNX Runtime 1.28.0

This file records the latest local checks. It is not a release-approval bundle
or a permanent evidence ledger; older detailed runs remain available in Git
history.

## Result

The move from ONNX Runtime 1.27.1 to 1.28.0 passes the repository's ordinary
local checks:

- `dart format --output=none --set-exit-if-changed lib bin hook test`: clean.
- `dart analyze --fatal-infos lib bin hook test`: no issues. Across the whole
  workspace the analyzer also reports two infos in `example/`, both the
  Flutter 3.50 deprecation of `package:flutter/material.dart`; `main` has
  them too.
- `dart test`: 245 passed, 19 environment-gated tests skipped.
- `python3 -B -m unittest discover -s tool/ci/tests -p 'test_*.py'`:
  603 passed.
- `python3 -B -m unittest discover -s tool/tests -p 'test_*.py'`:
  59 passed.
- `python3 -B tool/ci/check_c_source_quality.py --repository .`:
  39 C files passed.
- `python3 -B tool/ci/verify_native_c_abi_baseline.py --repository .`:
  the committed native ABI baseline passed.

With the verified `microsoft.ml.onnxruntime.1.28.0.nupkg` as the only file in
the offline cache:

- `tool/ci/check_macos_runtime.py` passed on the macOS library staged from it.
- `tool/ci/run_native_tests.py --suite posix` with that library: 30 CTests
  passed.
- `tool/ci/run_phase3_fixture_tests.py real-runtime --expected-ort-version
  1.28.0`: 2 CTests passed.
- `dart test --concurrency 1` with `FONIX_ORT_ARTIFACT_CACHE`,
  `FONIX_TEST_MACOS_ORT_ARCHIVE_DIR`, `FONIX_TEST_REAL_ORT_PATH`,
  `FONIX_TEST_SHIM_PATH`, `FONIX_TEST_ORT_PATH`,
  `FONIX_TEST_UNSUPPORTED_ORT_PATH`, and `FONIX_TEST_FAKE_ORT_PATH` set:
  290 passed, 2 skipped.
- `dart test test/portable_cross_build_test.dart` with `FONIX_TEST_ZIG`
  (Zig 0.17.1): the Linux x86_64/arm64 and Windows x64 bundles cross-built
  and passed their static audit.
- A clean external copy of `example/` passed all 78 Flutter tests.

Functional checks of the example built from an external copy with Flutter
3.50.0, outside the pinned reference gates:

- macOS Release app: `tool/ci/audit_apple_application.py` with
  `--run-cpu-probe` passed.
- iOS Simulator Debug app on iPhone 17 Pro / iOS 26.5: passing CPU receipt
  from the linked 1.28.0 runtime.
- Android Debug APK on an API 35 arm64 emulator with 16 KiB pages: passing
  CPU receipt from the bundled 1.28.0 runtime. A one-off byte comparison found
  every `PT_LOAD` segment of the packaged `libonnxruntime.so` identical to the
  locked library.

## Not run

- The macOS, iOS, and Android reference-application gates. They pin Flutter
  3.47.0-0.1.pre (the iOS gate also Xcode 26.6) and refused this host's
  toolchain.
- An Android Release/R8 build, AAB, or 4 KiB emulator run; the iOS device
  static audit; the Linux target-host gate; anything on Windows.
- `tool/ci/verify_bindings.py`: the vendored headers and generated bindings
  did not change.
- Re-pinning the iOS gate's two linked-shim digests
  (`_IOS_NORMALIZED_RUNTIME_FIELDS_SHA256`). They still hold the 1.27.1
  values, so that gate fails closed until a build under its pinned toolchain
  supplies new ones.
- Any physical device.

## Observed and unexplained

The Android XNNPACK smoke profile failed with `OrtProviderEvidenceException`
in the Debug/16 KiB/Flutter 3.50 setup above. A control build from the 1.27.1
lock failed the same way, so it is not caused by the runtime move.

Downloaded native archives and final packages continue to use their existing
size and SHA-256 integrity checks. Git owns the identity of tracked source and
benchmark fixture files.
