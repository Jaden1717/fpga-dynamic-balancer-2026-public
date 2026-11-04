"""HXS1-only stopped register evidence, using injected readers and no writes.

This observes a single-owner capture immediately after the original driver's
stop/drain. It neither loads an overlay nor repairs state to obtain a PASS.
"""
import csv
import hashlib
import json
from pathlib import Path

FORMAT = "hengxin-hxs1-stop-snapshot-v1"
XSA_SHA = "eca1b6e2c84d76b3f1ceabf1e30f4cbe0625e7c9551d2d977f8bfd12633dd08a"
FILES = ("samples.csv", "pulses.csv", "capture.json")
SYNC_REGISTERS = {"magic": 0, "abi_version": 4, "sync_ctrl": 8, "sync_status": 12,
                  "clock_hz": 0x10, "sample_depth": 0x14, "pulse_depth": 0x18,
                  "epoch": 0x1c, "sample_pending": 0x20, "pulse_pending": 0x24,
                  "sample_drops": 0x28, "pulse_drops": 0x2c,
                  "sample_highwater": 0x70, "pulse_highwater": 0x74,
                  "source_idle": 0x78, "source_missed": 0x7c}
ACQ_REGISTERS = {"acq_ctrl": 0x4000, "acq_status": 0x4004, "sample_total": 0x4008}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_hashes(directory):
    return {name: hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest() for name in FILES}


def read_snapshot(acq, sync):
    # Check the HXS1 identity before reading acquisition registers; these are
    # different from the local SYNC/ORD1 register layout.
    require(int(sync.rd(0)) == 0x48585331 and int(sync.rd(4)) == 65536,
            "expected HXS1 ABI v1")
    def read():
        state = {name: int(sync.rd(offset)) for name, offset in SYNC_REGISTERS.items()}
        state.update({name: int(acq.rd(offset)) for name, offset in ACQ_REGISTERS.items()})
        state["last_xyz_u32"] = [int(acq.rd(offset)) for offset in (0x4028, 0x402c, 0x4030)]
        return state
    return {"format": FORMAT, "read_only": True, "observations": [read(), read()]}


def check(snapshot, metadata, last_xyz, hashes):
    require(isinstance(snapshot, dict) and snapshot.get("format") == FORMAT
            and snapshot.get("read_only") is True, "not an HXS1 read-only snapshot")
    require(snapshot.get("file_sha256") == hashes, "snapshot raw file SHA mismatch")
    require(metadata.get("completed") is True and metadata.get("errors") == []
            and metadata.get("status") in ("PASS", "STATIC_ONLY"), "failed capture cannot pass stop check")
    require(metadata.get("xsa_sha256") == snapshot.get("xsa_sha256") == XSA_SHA,
            "HXS1 capture/snapshot XSA mismatch")
    require(type(metadata.get("abi_version")) is int and metadata["abi_version"] == 65536
            and type(metadata.get("clock_hz")) is int and metadata["clock_hz"] == 100000000,
            "HXS1 metadata ABI/clock mismatch")
    observations = snapshot.get("observations")
    require(isinstance(observations, list) and len(observations) == 2, "two register observations required")
    expected_fields = set(SYNC_REGISTERS) | set(ACQ_REGISTERS) | {"last_xyz_u32"}
    for state in observations:
        require(isinstance(state, dict) and set(state) == expected_fields, "snapshot register fields mismatch")
        for name, value in state.items():
            words = value if name == "last_xyz_u32" else [value]
            require(isinstance(words, list) and (name != "last_xyz_u32" or len(words) == 3)
                    and all(type(v) is int and 0 <= v < (1 << 32) for v in words),
                    "invalid uint32 snapshot register: " + name)
        require(state["magic"] == 0x48585331 and state["abi_version"] == 65536
                and state["clock_hz"] == 100000000, "snapshot identity/clock mismatch")
        for name in ("acq_ctrl", "sync_ctrl", "sample_pending", "pulse_pending",
                     "sample_drops", "pulse_drops", "source_missed"):
            require(state[name] == 0, "nonzero stopped register: " + name)
        require(not state["acq_status"] & 1 and state["sync_status"] == 6 and state["source_idle"] == 1,
                "HXS1 producer not idle or FIFOs not empty")
        require(type(metadata.get("epoch")) is int and state["epoch"] == metadata["epoch"],
                "snapshot epoch mismatch")
        count = metadata.get("sample_count")
        require(type(count) is int and count >= 2 and state["sample_total"] == count % (1 << 32),
                "snapshot source sample count mismatch")
        signed = [v - (1 << 32) if v & (1 << 31) else v for v in state["last_xyz_u32"]]
        require(signed == list(last_xyz), "snapshot LAST_XYZ mismatch")
        for prefix in ("sample", "pulse"):
            require(0 < state[prefix + "_depth"] and
                    state[prefix + "_highwater"] <= state[prefix + "_depth"], "invalid FIFO depth/highwater")
        pairs = {"hardware_status": "sync_status", "sample_fifo_depth": "sample_depth",
                 "pulse_fifo_depth": "pulse_depth", "sample_highwater": "sample_highwater",
                 "pulse_highwater": "pulse_highwater", "sample_drops": "sample_drops",
                 "pulse_drops": "pulse_drops", "source_missed": "source_missed"}
        for meta_key, state_key in pairs.items():
            if meta_key in metadata:
                require(type(metadata[meta_key]) is int and metadata[meta_key] == state[state_key],
                        "snapshot metadata mismatch: " + meta_key)
        require(metadata.get("source_idle") is True, "capture metadata source not idle")
    first, second = (dict(state) for state in observations)
    # Disabled DRDY level/legacy dropped sticky may change; enabled loss is
    # instead checked by SOURCE_MISSED plus the FIFO loss counters above.
    first["acq_status"] &= ~12
    second["acq_status"] &= ~12
    require(first == second, "registers changed during HXS1 snapshot")
    return {"verdict": "PASS", "samples": metadata["sample_count"], "epoch": metadata["epoch"],
            "scope": "single-owner HXS1 stop, empty FIFOs, source count/LAST_XYZ and bound raw files",
            "pulse_count_scope": "CSV continuity and empty FIFO; ABI exposes no independent lifetime pulse total",
            "physical_phase_validation": False}


def inspect(directory, acq, sync):
    directory = Path(directory)
    hashes = file_hashes(directory)
    metadata = json.loads((directory / "capture.json").read_text(encoding="utf-8-sig"))
    with (directory / "samples.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        require(next(reader, None) == ["frame_id", "ticks", "x_raw", "y_raw", "z_raw"], "wrong HXS1 columns")
        last = None
        count = 0
        for last in reader:
            count += 1
    snapshot = read_snapshot(acq, sync)
    snapshot.update(file_sha256=hashes, xsa_sha256=metadata.get("xsa_sha256"))
    try:
        require(count == metadata.get("sample_count") and last is not None, "saved sample count mismatch")
        snapshot["check"] = check(snapshot, metadata, tuple(map(int, last[2:])), hashes)
        require(hashes == file_hashes(directory), "raw files changed during snapshot")
        snapshot["verdict"] = "PASS"
    except (ValueError, KeyError, TypeError) as error:
        snapshot.update(verdict="FAIL", error=str(error))
    return snapshot
