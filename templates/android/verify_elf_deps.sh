#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: verify_elf_deps.sh <shared-library.so> <external|linked>

Prints ELF identity/dependencies/exports. In external mode it rejects a
DT_NEEDED dependency on libonnxruntime.so. This is a starter audit; integrate
its output into CI and add target-specific allowlists.
USAGE
}

if [[ $# -ne 2 ]]; then
  usage >&2
  exit 2
fi

library=$1
mode=$2

if [[ ! -f "$library" ]]; then
  echo "error: file not found: $library" >&2
  exit 2
fi
if [[ "$mode" != "external" && "$mode" != "linked" ]]; then
  echo "error: mode must be external or linked" >&2
  exit 2
fi

if command -v llvm-readelf >/dev/null 2>&1; then
  readelf=llvm-readelf
elif command -v readelf >/dev/null 2>&1; then
  readelf=readelf
else
  echo "error: llvm-readelf or readelf is required" >&2
  exit 2
fi

if command -v llvm-nm >/dev/null 2>&1; then
  nm=llvm-nm
elif command -v nm >/dev/null 2>&1; then
  nm=nm
else
  echo "error: llvm-nm or nm is required" >&2
  exit 2
fi

echo "== ELF header =="
"$readelf" -h "$library"

echo "== Program headers / alignment =="
"$readelf" -lW "$library"

echo "== Dynamic section =="
dynamic_output=$("$readelf" -dW "$library")
printf '%s\n' "$dynamic_output"

echo "== Dynamic exports =="
exports=$($nm -D --defined-only "$library" 2>/dev/null || true)
printf '%s\n' "$exports"

if [[ "$mode" == "external" ]] && grep -Eq '\(NEEDED\).*\[libonnxruntime\.so\]' <<<"$dynamic_output"; then
  echo "error: external/process shim has DT_NEEDED on libonnxruntime.so" >&2
  exit 1
fi

# Audit project exports. Toolchains may emit a few platform-generated symbols;
# tighten this allowlist for the selected NDK/linker.
# GNU/LLVM linkers may export the version-definition node itself (for
# example DORT_1.0) in addition to versioned dort_* symbols.
unexpected=$(awk 'NF {print $NF}' <<<"$exports" \
  | grep -Ev '^(dort_|DORT_[0-9]+(\.[0-9]+)*$|_?init$|_?fini$|__bss_start$|_edata$|_end$)' || true)
if [[ -n "$unexpected" ]]; then
  echo "error: unexpected exported symbols:" >&2
  printf '%s\n' "$unexpected" >&2
  exit 1
fi

echo "ELF dependency/export checks passed."
