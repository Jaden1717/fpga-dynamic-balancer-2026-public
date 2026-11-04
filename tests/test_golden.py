"""Behavioral checks for the handoff signal-chain contract."""
import importlib
import importlib.util
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim" / "golden"))


def module(name):
    assert importlib.util.find_spec(name) is not None, f"missing golden module: {name}"
    return importlib.import_module(name)


def csv_file(tmp_path, data="10,0.002500,0,0,1\n11,0.002750,0,0,1\n", **meta):
    values = {"fs_hz": "4000", "measured_sps": "4031.1", "devid": "0xAD"}
    values.update(meta)
    path = tmp_path / "capture.csv"
    path.write_text("".join(f"# {k}={v}\n" for k, v in values.items())
                    + "n,t_s,ax_g,ay_g,az_g\n" + data, encoding="ascii")
    return path


def test_csv_uses_measured_rate_and_retains_board_time(tmp_path):
    capture = module("board_csv").load(csv_file(tmp_path))
    assert capture["fs"] == 4031.1
    assert capture["nominal_fs"] == 4000
    assert capture["n"].tolist() == [10, 11]
    assert capture["t_board"].tolist() == [0.0025, 0.00275]
    assert capture["t"][1] - capture["t"][0] == pytest.approx(1 / 4031.1)
    assert capture["z"].tolist() == [1, 1]
    assert capture["meta"]["devid"] == "0xAD"


def test_csv_nominal_time_is_explicit_option(tmp_path):
    capture = module("board_csv").load(csv_file(tmp_path), rate="nominal")
    assert capture["fs"] == 4000
    assert capture["t"].tolist() == [0.0025, 0.00275]


@pytest.mark.parametrize("data", [
    "10,0.002500,0,0,1\n12,0.003000,0,0,1\n",
    "10,0.002500,0,0,1\n10,0.002500,0,0,1\n",
    "11,0.002750,0,0,1\n10,0.002500,0,0,1\n",
    "10.5,0.002500,0,0,1\n11,0.002750,0,0,1\n",
    "10,0.002500,nan,0,1\n11,0.002750,0,0,1\n",
    "10,0.002500,0,0,1\n11,0.999000,0,0,1\n",
    "",
])
def test_csv_rejects_corrupt_records(tmp_path, data):
    with pytest.raises(ValueError):
        module("board_csv").load(csv_file(tmp_path, data))


@pytest.mark.parametrize("meta", [
    {"chunk_gaps": "1"}, {"dropped": "1"}, {"frames": "3"},
    {"measured_sps": "nan"}, {"fs_hz": "0"},
])
def test_csv_rejects_bad_capture_metadata(tmp_path, meta):
    with pytest.raises(ValueError):
        module("board_csv").load(csv_file(tmp_path, **meta))


def test_csv_parser_handles_multiple_metadata_pairs_on_one_line(tmp_path):
    path = csv_file(tmp_path)
    path.write_text(path.read_text() + "# filter=0x00 range=0x81 power_ctl=0x00\n")
    capture = module("board_csv").load(path)
    assert capture["meta"]["range"] == "0x81"
    assert capture["meta"]["power_ctl"] == "0x00"


def test_fixed_frequency_estimator_recovers_phase_with_dc_and_partial_turns():
    fs, f0 = 4031.1, 28.3
    t = np.arange(537) / fs
    phase = math.radians(37)
    signal = 1.014 + 0.002 * np.cos(2 * np.pi * f0 * t + phase)
    estimate = module("board_csv").dft_1x(signal, fs, f0)
    assert estimate == pytest.approx(0.002 * np.exp(1j * phase), abs=1e-12)


@pytest.mark.parametrize("f0", [0, -1, 2000, float("nan")])
def test_fixed_frequency_estimator_rejects_invalid_frequency(f0):
    with pytest.raises(ValueError):
        module("board_csv").dft_1x(np.ones(100), 4000, f0)


def test_tacho_uses_pulse_span_and_pulses_per_rev():
    tacho = module("tacho")
    assert tacho.estimate_rot_speed_hz([0.1, 0.3, 0.5, 0.7]) == pytest.approx(5)
    assert tacho.estimate_rot_speed_hz([0.1, 0.3, 0.5, 0.7], 2) == pytest.approx(2.5)
    with pytest.raises(ValueError):
        tacho.estimate_rot_speed_hz([0, 0])


def test_angle_domain_recovers_phase_during_speed_variation():
    sync = module("sync_order")
    fs = 4000
    t = np.arange(4001) / fs
    pulses = np.array([0.01, 0.052, 0.091, 0.127, 0.16, 0.19, 0.217, 0.241])
    phase_t = np.interp(t, pulses, np.arange(len(pulses)) * 2 * np.pi)
    phase = math.radians(-43)
    signal = 0.02 * np.cos(phase_t + phase) + 0.005 * np.cos(2 * phase_t)
    samples = sync.to_angle_domain(t, signal, pulses, pts_per_rev=64)
    estimate = sync.extract_1x(samples, pts_per_rev=64)
    assert abs(estimate) == pytest.approx(0.02, rel=0.02)
    assert np.angle(estimate * np.exp(-1j * phase)) == pytest.approx(0, abs=0.01)


def test_angle_domain_rejects_missing_or_out_of_bounds_pulses():
    sync = module("sync_order")
    t = np.arange(1000) / 4000
    for pulses in ([0.01], [0.1, 0.09], [-0.1, 0.1], [0.1, 0.5]):
        with pytest.raises(ValueError):
            sync.to_angle_domain(t, np.ones(1000), pulses)


def test_trial_weight_returns_mass_and_angle_with_different_install_radius():
    balance = module("balance")
    u0 = 60 * np.exp(1j * math.radians(130))
    trial = 30 + 0j
    response = 0.008 * np.exp(1j * math.radians(15))
    result = balance.solve(response * u0, response * (u0 + trial), trial, 20)
    assert result["mass_g"] == pytest.approx(3)
    assert result["angle_deg"] == pytest.approx(310)
    assert result["correction_gmm"] == pytest.approx(-u0)
    with pytest.raises(ValueError):
        balance.solve(1 + 0j, 1 + 0j, trial, 20)


def test_synthetic_closed_loop_uses_complete_signal_chain():
    model, sync = module("rotor_model"), module("sync_order")
    balance, tacho = module("balance"), module("tacho")
    u0 = 60 * np.exp(1j * math.radians(130))
    trial = 30 + 0j
    base = model.make_measurement(u0, fs=4000, f_rot_hz=47.5, noise_fraction=0.02)
    weighted = model.make_measurement(u0 + trial, fs=4000, f_rot_hz=47.5,
                                      noise_fraction=0.02, seed=2)
    v0 = sync.extract_1x(sync.to_angle_domain(base["t"], base["signal"], base["pulses"]))
    v1 = sync.extract_1x(sync.to_angle_domain(weighted["t"], weighted["signal"],
                                             weighted["pulses"]))
    result = balance.solve(v0, v1, trial, 30)
    assert abs(u0 + result["correction_gmm"]) / abs(u0) < 0.05
    assert tacho.estimate_rot_speed_hz(base["pulses"]) == pytest.approx(47.5)


def test_csv_cli_requires_supplied_frequency_for_1x(tmp_path):
    module("board_csv")
    path = csv_file(tmp_path)
    run = subprocess.run([sys.executable, str(ROOT / "sim/golden/board_csv.py"), str(path)],
                         text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert "4031.1" in run.stdout
    assert "1x" not in run.stdout.lower()


def test_csv_cli_exits_nonzero_for_corrupt_data(tmp_path):
    module("board_csv")
    path = csv_file(tmp_path, "10,0.002500,0,0,1\n12,0.003000,0,0,1\n")
    run = subprocess.run([sys.executable, str(ROOT / "sim/golden/board_csv.py"), str(path)],
                         text=True, capture_output=True)
    assert run.returncode != 0

def test_real_static_csv_matches_handoff_fingerprint_and_statistics():
    import hashlib
    path = ROOT / "data/adxl355_static_20261006.csv"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "41d24581e2daad6a0bd44e1db4352b1951dc8044c50fe3d33941281d7de1dab5")
    capture = module("board_csv").load(path)
    assert len(capture["n"]) == 20409
    assert capture["n"][[0, -1]].tolist() == [2040, 22448]
    assert capture["fs"] == 4026.7
    for name, expected in (("x", -0.0381), ("y", 0.0070), ("z", 1.0140)):
        assert np.mean(capture[name]) == pytest.approx(expected, abs=0.00005)


def test_time_and_angle_estimators_share_complex_amplitude_convention():
    sync = module("sync_order")
    t = np.arange(8000) / 4000
    pulses = np.arange(95) / 47.5
    expected = 0.013 * np.exp(1j * math.radians(73))
    signal = 1.014 + np.real(expected * np.exp(2j * np.pi * 47.5 * t))
    time_v = module("board_csv").dft_1x(signal, 4000, 47.5)
    angle_v = sync.extract_1x(sync.to_angle_domain(t, signal, pulses))
    assert time_v == pytest.approx(expected, abs=1e-12)
    assert abs(angle_v - time_v) / abs(time_v) < 0.02


def test_demo_cli_reports_closed_loop_and_writes_json(tmp_path):
    import json
    script = ROOT / "sim/golden/run_demo.py"
    assert script.exists(), "missing runnable complete-chain demo"
    output = tmp_path / "demo.json"
    run = subprocess.run([sys.executable, str(script), "--output", str(output)],
                         text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    result = json.loads(output.read_text())
    assert result["rotation_hz"] == pytest.approx(47.5)
    assert result["relative_residual"] < 0.05
    assert result["mass_g"] == pytest.approx(2, rel=0.02)
    assert result["angle_deg"] == pytest.approx(310, abs=2)
    assert result["measurement_kind"] == "synthetic"
