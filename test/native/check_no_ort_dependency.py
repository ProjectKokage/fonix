#!/usr/bin/env python3
"""Reject a link-time ONNX Runtime dependency in the external shim."""

from __future__ import annotations

import platform
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_no_ort_dependency.py <native-library>", file=sys.stderr)
        return 2
    if platform.system() == "Darwin":
        command = ["otool", "-L", sys.argv[1]]
    else:
        command = ["readelf", "-dW", sys.argv[1]]
    output = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if "onnxruntime" in output.lower():
        print("external shim has a link-time ONNX Runtime dependency", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1
    print("External shim has no link-time ONNX Runtime dependency.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
