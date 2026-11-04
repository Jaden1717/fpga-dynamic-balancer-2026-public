#!/usr/bin/env bash
set -euo pipefail
hx_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
hx_python=/usr/local/share/pynq-venv/bin/python3
if [[ ! -x "$hx_python" ]]; then
    printf '%s\n' "Required PYNQ interpreter is unavailable: $hx_python" >&2
    exit 126
fi
exec "$hx_python" "$hx_script_dir/../sim/golden/measurement_run.py" "$@"
