# Fixed input FPGA replay — 2026-11-01

Keep the fan OFF and the C tape unchanged. This step acquires no new sensor data.
The first record of the three-run pilot is selected by acquisition order; all eight
128-turn Z AA128-to64 windows are run, without choosing the best CV/window.

Requires the unchanged sibling USB folder `hx_deadline_20261031_01`, including its
verified raw return and software ZIP, and the already installed compute XSA.

```bash
sudo mkdir -p /mnt/hxusb
mountpoint -q /mnt/hxusb || sudo mount -L MOCLIFFOOD /mnt/hxusb
sudo bash /mnt/hxusb/hx_core_replay_20261101_01/start_fpga_replay.sh
```

After the command returns to the shell:

```bash
sudo umount /mnt/hxusb
```

Return the USB to the PC with the final summary and SHA256. Any error stops the
batch; all partial files remain. Do not delete existing results or rerun blindly.
The command uses the fixed PYNQ venv, verifies the two package manifests and known
archive/XSA hashes and the five frozen source/context file hashes, extracts tools to RAM, and writes new evidence only to USB.
It retains exact prepared input/result/verification for eight FPGA windows, with
MMIO feed, result wait, snapshot and overlay timing. Wait includes software/MMIO
overhead and is not pure PL latency. No speedup or calibrated physical correction
is established just by arithmetic agreement. The original runtime context in the
review summary describes acquisition; execution_context_before/after describes
this later FPGA replay. A replay boot may differ from the original capture boot.
