#!/usr/bin/env bash
set -euo pipefail
hx_bundle="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
hx_source="$(cd -- "$hx_bundle/../hx_deadline_20261031_01" && pwd)"
hx_python=/usr/local/share/pynq-venv/bin/python3
hx_tools=/dev/shm/hx_fpga_replay_tools_20261101_01
hx_xsa=/home/xilinx/validation/order1x_20261016_v1/order1x.xsa
hx_return="$hx_bundle/return_fpga_replay_01"
hx_archive="$hx_bundle/evidence_fpga_replay_01.tar.gz"
if [[ ! -x "$hx_python" ]]; then printf '%s\n' "PYNQ interpreter missing: $hx_python" >&2; exit 126; fi
if [[ "$(findmnt -n -o FSTYPE -T "$hx_bundle")" != vfat ]]; then printf '%s\n' 'Run from the mounted USB vfat volume.' >&2; exit 1; fi
if [[ -e "$hx_tools" || -e "$hx_return" || -e "$hx_archive" ]]; then
    printf '%s\n' 'Output/tools already exists. Existing evidence is preserved. Stop and report; do not remove it.' >&2; exit 1
fi
(cd -- "$hx_bundle" && sha256sum -c SHA256SUMS)
(cd -- "$hx_source" && sha256sum -c SHA256SUMS)
(cd -- "$hx_source" && sha256sum -c "$hx_bundle/reference_SHA256SUMS")
printf '%s  %s\n' e78b7b51998a808b2fb490f4aeb1ae94eb6f15550fbf022fa0469f99547af599 "$hx_source/evidence_reference_01.tar.gz" | sha256sum -c -
printf '%s  %s\n' 980107bfd3775bdf060086d69834d3c7e17c0725f332a069fcccae734463b346 "$hx_xsa" | sha256sum -c -
"$hx_python" - "$hx_source/unified_measurement_20261031_v1.zip" "$hx_tools" <<'PY'
from pathlib import Path,PurePosixPath
import sys,zipfile
archive,target=Path(sys.argv[1]),Path(sys.argv[2])
with zipfile.ZipFile(archive) as stream:
    assert stream.testzip() is None
    for name in stream.namelist():
        p=PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or ':' in name or '\\' in name:raise ValueError('unexpected ZIP member')
    target.mkdir()
    stream.extractall(target)
PY
mkdir -- "$hx_return"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$hx_tools/sim/golden${PYTHONPATH:+:$PYTHONPATH}"
"$hx_python" - "$hx_return/execution_context_before.json" "$hx_xsa" <<'PY'
from pathlib import Path
import json,sys
from measurement_run import observe_runtime
v=dict(kind='hengxin-fpga-replay-execution-v1',runtime=observe_runtime(),compute_xsa_path=sys.argv[2],source_archive_sha256='e78b7b51998a808b2fb490f4aeb1ae94eb6f15550fbf022fa0469f99547af599',selection='run_01, all eight fixed windows; selected by acquisition order, not lowest CV',new_physical_capture=False,physical_validation_complete=False)
with Path(sys.argv[1]).open('x',encoding='utf8') as f:json.dump(v,f,indent=2);f.write('\n')
PY
hx_finish() {
    hx_exit=$?
    trap - EXIT
    set +e
    "$hx_python" - "$hx_return/execution_context_after.json" "$hx_exit" "$hx_return/review/summary.json" <<'PY'
from pathlib import Path
import json,sys
from measurement_run import observe_runtime
v=dict(runtime=observe_runtime(),command_exit_code=int(sys.argv[2]),new_physical_capture=False)
with Path(sys.argv[1]).open('x',encoding='utf8') as f:json.dump(v,f,indent=2);f.write('\n')
p=Path(sys.argv[3])
if p.exists():
    s=json.loads(p.read_text());m=s['measurement']
    print('CORE_REPLAY: verified FPGA windows=%d; mean=%.6f mg peak' % (m['hardware_windows_verified'],m['mean_peak_mg']),flush=True)
    print('FPGA feed/result-wait/snapshot timing is saved in review/summary.json.',flush=True)
PY
    if ! tar -czf "$hx_archive" -C "$hx_bundle" return_fpga_replay_01; then
        printf '%s\n' 'Archive failed; originals remain in return_fpga_replay_01. Report before removing USB.' >&2; exit 1
    fi
    if ! sync -f "$hx_archive"; then printf '%s\n' 'USB sync failed. Report before removing USB.' >&2; exit 1; fi
    printf '\n%s\n' 'Evidence archive:'
    sha256sum "$hx_archive"
    printf '\n%s\n' 'Keep fan OFF. After return to shell: sudo umount /mnt/hxusb'
    if [[ "$hx_exit" -ne 0 ]]; then printf '%s\n' 'Stopped on first error; partial evidence retained. No retry.'; fi
    exit "$hx_exit"
}
trap hx_finish EXIT
printf '\n%s\n' 'Keep fan OFF and tape at C. Replaying existing run_01; no physical capture.'
"$hx_python" -u "$hx_tools/sim/golden/measurement_run.py" review \
    "$hx_source/return_reference_01/run_01/raw" \
    --runtime "$hx_source/return_reference_01/run_01/runtime.json" \
    --avg-log2 7 --compute-xsa "$hx_xsa" --out "$hx_return/review" \
    2>&1 | tee "$hx_return/console.txt"
