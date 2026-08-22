# Validation report

- Date: 2026-08-23
- Host: macOS arm64
- Package: `fonix 0.1.0-dev.1`

This file records the latest local checks. It is not a release-approval bundle
or a permanent evidence ledger; older detailed runs remain available in Git
history.

## Result

The cleanup passes the repository's ordinary local checks:

- `dart analyze --fatal-infos`: no issues.
- `dart test`: 217 passed, 19 environment-gated tests skipped.
- `python3 -B -m unittest discover -s tool/ci/tests -p 'test_*.py'`:
  591 passed.
- `python3 -B -m unittest discover -s tool/tests -p 'test_*.py'`:
  59 passed.
- `python3 -B tool/ci/check_c_source_quality.py --repository .`:
  39 C files passed.
- `python3 -B tool/ci/verify_native_c_abi_baseline.py --repository .`:
  the committed native ABI baseline passed.
- A clean external copy of `example/` passed `flutter analyze --fatal-infos`
  and all 78 Flutter tests.

The opt-in CPU benchmark was exercised through injected test fakes. No timing
baseline or device-performance claim was made.

## Not run

This local pass did not provision or execute a real ONNX Runtime model, build a
final distributable package, run an emulator or simulator, or test a physical
device. It therefore makes no new native-runtime, provider, target-platform,
performance, signing, or distribution claim.

Downloaded native archives and final packages continue to use their existing
size and SHA-256 integrity checks. Git owns the identity of tracked source and
benchmark fixture files.
