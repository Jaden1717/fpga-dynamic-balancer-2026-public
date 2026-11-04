#!/usr/bin/env bash
set -euo pipefail
hx_bundle="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
hx_python=/usr/local/share/pynq-venv/bin/python3
hx_tools=/dev/shm/hx_control_tools_20261101_01
hx_return="$hx_bundle/return_control_01"
hx_archive="$hx_bundle/evidence_control_01.tar.gz"
if [[ ! -x "$hx_python" ]]; then printf '%s\n' "PYNQ interpreter missing: $hx_python" >&2; exit 126; fi
if [[ "$(findmnt -n -o FSTYPE -T "$hx_bundle")" != vfat ]]; then printf '%s\n' 'Run from the mounted USB vfat volume.' >&2; exit 1; fi
if [[ -e "$hx_tools" || -e "$hx_return" || -e "$hx_archive" ]]; then
    printf '%s\n' 'Existing output/tools preserved. Stop and report; do not remove existing evidence.' >&2; exit 1
fi
(cd -- "$hx_bundle" && sha256sum -c SHA256SUMS)
"$hx_python" - "$hx_bundle/unified_measurement_20261031_v1.zip" "$hx_tools" <<'PY'
from pathlib import Path,PurePosixPath
import sys,zipfile
archive,target=Path(sys.argv[1]),Path(sys.argv[2])
with zipfile.ZipFile(archive) as stream:
    assert stream.testzip() is None
    for name in stream.namelist():
        p=PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or ':' in name or '\\' in name:raise ValueError('unexpected ZIP member')
    target.mkdir();stream.extractall(target)
PY
mkdir -- "$hx_return"
cp -- "$hx_bundle/protocol.json" "$hx_return/protocol.json"
export PYTHONDONTWRITEBYTECODE=1
hx_finish() {
    hx_exit=$?
    trap - EXIT
    set +e
    "$hx_python" "$hx_bundle/finish_control.py" "$hx_return"
    hx_summary_exit=$?
    if [[ "$hx_exit" -eq 0 && "$hx_summary_exit" -ne 0 ]]; then hx_exit="$hx_summary_exit"; fi
    if ! tar -czf "$hx_archive" -C "$hx_bundle" return_control_01; then
        printf '%s\n' 'Archive failed; originals remain in return_control_01. Report before removing USB.' >&2; exit 1
    fi
    if ! sync -f "$hx_archive"; then printf '%s\n' 'USB sync failed; report before removing USB.' >&2; exit 1; fi
    printf '\n%s\n' 'Evidence archive:'
    sha256sum "$hx_archive"
    printf '\n%s\n' 'Turn fan OFF and wait until stopped. After return to shell: sudo umount /mnt/hxusb'
    if [[ "$hx_exit" -ne 0 ]]; then printf '%s\n' 'Stopped; partial evidence retained. No retry or added capture.'; fi
    exit "$hx_exit"
}
trap hx_finish EXIT
printf '\n%s\n' 'FIVE-STATE CONTROL. Keep sensor, support, base, cable routing and power setting unchanged.'
printf '%s\n' 'All tape operations: fan OFF and rotor fully stopped. Leave board powered during this batch.'
printf '%s\n' 'Choose ONE viewing face, and retain it for angle numbering throughout:'
printf '%s\n' '  1 = motor-label and fixed-strut side; 2 = opposite side.'
read -r -p 'Viewing face (1 or 2): ' hx_face
case "$hx_face" in 1|2) ;; *) printf '%s\n' 'Unknown face; no capture will start.' >&2; exit 1;; esac
printf '\n%s\n' 'PREPARE BEFORE FIRST RECORD:'
printf '%s\n' 'Turn fan OFF and wait until stopped. Mark the CURRENT C BLADE as p0.'
printf '%s\n' 'From this same viewing face, count clockwise on the 9-blade rotor:'
printf '%s\n' 'blade 1=p0 (0 deg), blade 4=p120, blade 7=p240. These labels stay with the rotor blades.'
printf '%s\n' 'With a ruler, mark all THREE tape-center targets at approximately 40 mm radius.'
printf '%s\n' 'Keep all position marks throughout the batch. Use the same blade surface as the original C tape.'
printf '%s\n' 'Remove that same C tape intact for the first NO-TRIAL record; retain it cleanly for reuse.'
printf '%s\n' 'If damaged, curled, not adhesive, or access touches the sensor/pulls cable: enter STOP.'
read -r -p 'After all preparation is complete, type READY (anything else stops): ' hx_ready
if [[ "$hx_ready" != READY ]]; then printf '%s\n' 'Preparation not declared complete; stopped without capture.'; exit 1; fi
"$hx_python" - "$hx_return" "$hx_face" <<'PY'
from pathlib import Path
import json,sys
root=Path(sys.argv[1]);face={'1':'motor-label and fixed-strut side','2':'opposite to motor-label side'}[sys.argv[2]]
geometry=dict(kind='hengxin-operator-trial-geometry-v1',viewing_face=face,positive_direction='clockwise in selected face',p0='original C blade at start of batch',p120='blade4 clockwise from p0 as blade1',p240='blade7 clockwise from p0 as blade1',angles_deg=[0,120,240],radius_mm_approximately=40,actual_radius_tolerance_mm=None,marking_method='operator-declared ruler marks at all three tape centers',same_tape_piece=True,absolute_trial_mass_g=None,same_blade_surface=True,position_marks_present_for_all_five_states=True,access_without_sensor_touch_or_cable_pull='operator confirmed in chat and declared READY',fixture='previous arrangement reinforced before this batch',independently_verified=False)
with (root/'geometry.json').open('x',encoding='utf8') as f:json.dump(geometry,f,indent=2);f.write('\n')
for name,pos in [('01_reference_before','none'),('02_trial_000','p0'),('03_trial_120','p120'),('04_trial_240','p240'),('05_reference_after','none')]:
    setup=dict(fixture_id='reinforced_original_20261101_01',sensor_mount_id='unchanged_reinforced_mount_20261101_01',cable_relief='operator-declared no touch or cable pull during trial access',environment=None,trial_position=pos,trial_mass_g=0 if pos=='none' else None,trial_radius_mm=None if pos=='none' else 40,power_setting=None)
    with (root/('setup_'+name+'.json')).open('x',encoding='utf8') as f:json.dump(setup,f,indent=2);f.write('\n')
PY
for hx_index in 1 2 3 4 5; do
    case "$hx_index" in
        1) hx_name=01_reference_before; hx_instruction='NO trial tape on any blade; retain the intact piece for reuse.';;
        2) hx_name=02_trial_000; hx_instruction='Same intact tape at marked p0 (original C blade), center at the 40 mm target.';;
        3) hx_name=03_trial_120; hx_instruction='Move that SAME piece to marked p120, center at the 40 mm target.';;
        4) hx_name=04_trial_240; hx_instruction='Move that SAME piece to marked p240, center at the 40 mm target.';;
        5) hx_name=05_reference_after; hx_instruction='Remove that piece intact; NO trial tape on any blade. Keep all position marks.';;
    esac
    printf '\nState %s/5: %s\n%s\n' "$hx_index" "$hx_name" "$hx_instruction"
    printf '%s\n' 'Keep fan OFF/stopped while placing. Keep tape flat, sensor/base/cables untouched.'
    read -r -p 'Enter READY when this state is set; anything else stops: ' hx_ready
    printf 'state=%s placement_response=%s\n' "$hx_name" "$hx_ready" >> "$hx_return/operator_steps.log"
    if [[ "$hx_ready" != READY ]]; then printf '%s\n' 'Placement not declared ready; no new capture.'; exit 1; fi
    read -r -p 'Turn fan ON, then press Enter. A fixed 30-second wait will follow: ' hx_reply
    sleep 30
    "$hx_python" -u "$hx_tools/sim/golden/measurement_run.py" capture \
        --peer-directory /home/xilinx/validation/acq_sync_20261015_v1 \
        --evidence-directory /home/xilinx/validation/hxs1_stop_20261016_v1 \
        --xsa /home/xilinx/validation/acq_sync_20261015_v1/acq_sync.xsa \
        --seconds 40 --setup "$hx_return/setup_$hx_name.json" --out "$hx_return/$hx_name" \
        2>&1 | tee "$hx_return/capture_$hx_name.log"
    read -r -p 'Turn fan OFF and wait until fully stopped, then press Enter: ' hx_reply
    printf 'state=%s operator_confirmed_fan_off_and_stopped\n' "$hx_name" >> "$hx_return/operator_steps.log"
done
