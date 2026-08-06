#!/usr/bin/env python3
"""Audit the Windows external shim's x64 PE dependencies and exports."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


EXPORT_LINE = re.compile(
    r"^\s*\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*$"
)
DEF_SYMBOL = re.compile(r"^\s*(dort_[A-Za-z0-9_]+)\s*$")


class WindowsBinaryError(RuntimeError):
    """The PE binary does not match the checked-in external-shim contract."""


def parse_expected_exports(source: str) -> frozenset[str]:
    lines = source.splitlines()
    try:
        exports_index = next(
            index for index, line in enumerate(lines) if line.strip() == "EXPORTS"
        )
    except StopIteration as error:
        raise WindowsBinaryError("module-definition file has no EXPORTS section") from error
    symbols: list[str] = []
    for line in lines[exports_index + 1 :]:
        if not line.strip() or line.lstrip().startswith(";"):
            continue
        match = DEF_SYMBOL.fullmatch(line)
        if match is None:
            raise WindowsBinaryError(
                f"unsupported module-definition export line: {line!r}"
            )
        symbols.append(match.group(1))
    if not symbols or len(symbols) != len(set(symbols)):
        raise WindowsBinaryError(
            "module-definition exports must be non-empty and unique"
        )
    return frozenset(symbols)


def parse_dumpbin_exports(source: str) -> frozenset[str]:
    symbols = [
        match.group(1)
        for line in source.splitlines()
        if (match := EXPORT_LINE.fullmatch(line)) is not None
    ]
    if not symbols or len(symbols) != len(set(symbols)):
        raise WindowsBinaryError("dumpbin exports were empty or duplicated")
    return frozenset(symbols)


def validate_dumpbin_reports(
    *,
    exports_report: str,
    dependents_report: str,
    headers_report: str,
    expected_exports: frozenset[str],
) -> None:
    actual_exports = parse_dumpbin_exports(exports_report)
    missing = expected_exports - actual_exports
    unexpected = actual_exports - expected_exports
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing exports: " + ", ".join(sorted(missing)))
        if unexpected:
            details.append("unexpected exports: " + ", ".join(sorted(unexpected)))
        raise WindowsBinaryError("; ".join(details))
    if "onnxruntime" in dependents_report.lower():
        raise WindowsBinaryError(
            "external Windows shim has a link-time ONNX Runtime dependency"
        )
    if re.search(r"\bmachine\s*\(x64\)", headers_report, re.IGNORECASE) is None:
        raise WindowsBinaryError("Windows CI shim is not an x64 PE image")


def _dumpbin(executable: Path, option: str, library: Path) -> str:
    try:
        result = subprocess.run(
            [str(executable), option, str(library)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise WindowsBinaryError(f"dumpbin {option} failed") from error
    return result.stdout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dumpbin", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        expected = parse_expected_exports(
            arguments.exports.read_text(encoding="utf-8")
        )
        validate_dumpbin_reports(
            exports_report=_dumpbin(arguments.dumpbin, "/exports", arguments.library),
            dependents_report=_dumpbin(
                arguments.dumpbin, "/dependents", arguments.library
            ),
            headers_report=_dumpbin(arguments.dumpbin, "/headers", arguments.library),
            expected_exports=expected,
        )
    except (OSError, WindowsBinaryError) as error:
        print(f"Windows binary verification failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Windows x64 external shim matches {len(expected)} locked exports and has no ORT import."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
