#!/usr/bin/env bash
set -euo pipefail
hx_bundle="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
hx_python=/usr/local/share/pynq-venv/bin/python3
hx_tools=/dev/shm/hx_measure_tools_20261031_01
hx_return="$hx_bundle/return_reference_01"
hx_archive="$hx_bundle/evidence_reference_01.tar.gz"
if [[ ! -x "$hx_python" ]]; then printf '%s\n' "PYNQ interpreter missing: $hx_python" >&2; exit 126; fi
if [[ "$(findmnt -n -o FSTYPE -T "$hx_bundle")" != vfat ]]; then printf '%s\n' 'Run this script from the mounted USB vfat volume.' >&2; exit 1; fi
if [[ -e "$hx_tools" || -e "$hx_return" || -e "$hx_archive" ]]; then
    printf '%s\n' 'Output/tools already exists. Existing evidence is preserved. Stop and report this message; do not remove it.' >&2; exit 1
fi
(cd -- "$hx_bundle" && sha256sum -c SHA256SUMS)
"$hx_python" - "$hx_bundle/unified_measurement_20261031_v1.zip" "$hx_tools" <<'PY'
from pathlib import Path
import sys,zipfile
archive,target=Path(sys.argv[1]),Path(sys.argv[2]);target.mkdir()
with zipfile.ZipFile(archive) as stream:
    for name in stream.namelist():
        p=Path(name)
        if p.is_absolute() or '..' in p.parts:raise ValueError('unexpected ZIP member')
    stream.extractall(target)
PY
mkdir -- "$hx_return"
hx_finish() {
    hx_exit=$?
    trap - EXIT
    set +e
    "$hx_python" "$hx_bundle/finish_reference.py" "$hx_return"
    if tar -czf "$hx_archive" -C "$hx_bundle" return_reference_01; then
        sync -f "$hx_archive"
        printf '\n%s\n' 'Evidence archive:'
        sha256sum "$hx_archive"
    else
        printf '%s\n' 'Archive creation failed; original files remain under return_reference_01. Report the error before removing USB.' >&2
        exit 1
    fi
    printf '\n%s\n' 'Keep the fan OFF. No new run will start.'
    printf '%s\n' 'After this script returns to the shell, run: sudo umount /mnt/hxusb'
    if [[ "$hx_exit" -ne 0 ]]; then printf '%s\n' 'Batch stopped on the first error; partial evidence retained. No retry.'; fi
    exit "$hx_exit"
}
trap hx_finish EXIT
printf '\n%s\n' 'Keep trial, sensor, base and cables unchanged throughout this batch.'
read -r -p 'Turn fan OFF and wait until fully stopped. Press Enter when ready: ' hx_reply
for hx_run in 1 2 3; do
    printf '\nRun %s/3\n' "$hx_run"
    read -r -p 'Turn fan ON, then press Enter. The script will wait 30 seconds: ' hx_reply
    sleep 30
    hx_name="$(printf 'run_%02d' "$hx_run")"
    "$hx_python" -u "$hx_tools/sim/golden/measurement_run.py" capture \
        --peer-directory /home/xilinx/validation/acq_sync_20261015_v1 \
        --evidence-directory /home/xilinx/validation/hxs1_stop_20261016_v1 \
        --xsa /home/xilinx/validation/acq_sync_20261015_v1/acq_sync.xsa \
        --seconds 40 --setup "$hx_bundle/setup_reference.json" --out "$hx_return/$hx_name"
    read -r -p 'Turn fan OFF and wait until fully stopped. Press Enter when ready: ' hx_reply
done
