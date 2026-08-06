#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository=$(cd -- "$script_dir/../.." && pwd)

exec python3 -B "$repository/tool/ci/build_sherpa_aligned_android.py" "$@"
