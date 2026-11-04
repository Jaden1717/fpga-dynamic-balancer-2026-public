"""Recheck the preserved eight-window FPGA run; performs no hardware access."""
from pathlib import Path
import hashlib,json,sys
repo=Path(__file__).resolve().parents[3]
sys.dont_write_bytecode=True
sys.path.insert(0,str(repo/'sim/golden'))
from verify_order1x import verify
root=repo/'data/physical/hx_20261101_002/board_export/return_fpga_replay_01/review/fpga_evidence'
for line in (root/'sha256sums.txt').read_text().splitlines():
    want,name=line.split(maxsplit=1)
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==want
for start in range(0,1024,128):
    p=root/f'input_{start}.json';q=root/f'result_{start}.json'
    actual=verify(p,q)
    assert actual==json.loads((root/f'verification_{start}.json').read_text())
    original=json.loads((repo/f'data/review/hx_fixed_c_reference_20261101/prepared/run_01/input_{start:04d}.json').read_text())
    prepared=json.loads(p.read_text())
    assert prepared['samples_raw']==original['samples_raw']
    assert prepared['source_sha256']==original['source_sha256']
    print(f'{start}: exact I/Q; magnitude error={actual["magnitude_error_q8"]} LSB; phase error={actual["phase_error_deg"]:.9g} deg')
print('8/8 verified; original prepared words matched; no hardware execution.')
