#!/usr/bin/env python3
"""Generate a closed static sherpa-owned Android APK/AAB package record.

This command accepts no runtime receipt. It binds one exact APK and one
native-matched base-only AAB to the same selected sherpa Flutter FFI and Fonix
wrapper native inputs. It does not establish application/version manifest
identity. Runtime/load-order evidence remains the responsibility of the
APK-only compatibility-manifest path.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


sys.dont_write_bytecode = True

from android_compatibility_manifest import (
    BUILD_TYPES,
    CompatibilityManifestError,
    VERIFIER,
    _write_new,
    generate_static_package_manifest,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(
        mode="sherpa-owned",
        sherpa_library_profile="flutter-ffi",
    )
    parser.add_argument("--sherpa-source", required=True)
    parser.add_argument("--sherpa-revision", required=True)
    parser.add_argument(
        "--sherpa-artifact", action="append", type=Path, required=True
    )
    parser.add_argument(
        "--wrapper-artifact", action="append", type=Path, required=True
    )
    parser.add_argument("--final-apk", type=Path, required=True)
    parser.add_argument("--final-aab", type=Path, required=True)
    parser.add_argument(
        "--abi",
        action="append",
        choices=sorted(VERIFIER.ANDROID_ABIS),
        required=True,
    )
    parser.add_argument("--ort-api-required", type=int, required=True)
    parser.add_argument("--build-type", choices=sorted(BUILD_TYPES), required=True)
    parser.add_argument("--snapshot-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        manifest = generate_static_package_manifest(
            arguments,
            generator_path=Path(__file__),
        )
        _write_new(arguments.output, manifest)
    except (CompatibilityManifestError, FileNotFoundError, OSError) as error:
        print(f"android_static_package_manifest: {error}", file=sys.stderr)
        return 1
    print(f"Wrote Android static package manifest: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
