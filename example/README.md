# Fonix Flutter reference application

This app exercises Fonix only through `package:fonix/fonix.dart`. It provides
the small CPU smoke path used by the platform gates, Android XNNPACK functional
checks, and a local CPU benchmark. It is a development reference, not a release
artifact.

The platform baselines are macOS arm64 14.0, iOS 15.1, Android API 24, and
Linux x86_64 with glibc 2.27. Each platform gate states the narrower host,
device, signing, and packaging evidence it actually proves.

## Build from an external copy

Do not build this directory in place. Native-asset output for a consuming app
must live outside the Fonix package checkout. The platform gates copy the
tracked source to a temporary directory before running Flutter and keep all
generated files there.

## Desktop smoke path

On macOS and Linux, setting `FONIX_REFERENCE_SMOKE=1` runs one deterministic
CPU inference and exits after writing the bounded reference receipt. This path
has precedence over the benchmark mode.

The iOS gate uses a resident launch channel instead of passing the environment
map into Dart. Android uses its dedicated bounded profile channel.

## CPU benchmark

On a macOS or Linux build, set `FONIX_CPU_BENCHMARK=1` to run the ordinary CPU
benchmark. It performs three warm-up inferences followed by ten measured
inferences, checks every output against the tracked float32 reference, and
writes one line beginning with `FONIX_CPU_BENCHMARK_RESULT=`.

The result contains the raw microsecond samples plus min, median, average, and
max. It is a local measurement, not a signed receipt, baseline, qualification,
or release gate. Record the device, OS, runtime, build mode, and power state
alongside any number you intend to compare.

## Platform gates

The gate scripts accept absolute paths and expose their current options through
`--help`:

```sh
python3 -B tool/ci/run_macos_reference_app_gate.py --help
python3 -B tool/ci/run_ios_reference_app_gate.py --help
python3 -B tool/ci/run_android_reference_app_gate.py --help
python3 -B tool/ci/run_android_sherpa_reference_app_gate.py --help
python3 -B tool/ci/run_linux_reference_app_gate.py --help
```

The macOS and Android Release builds use development-only signing. The iOS
project commits no development team or provisioning identity. Simulator,
cross-build, static-audit, and unsigned output each prove only their named
layer; physical-device and distribution qualification remain separate.
