"""MMIO is the hardware boundary; FIFO head/pop effects are modeled here."""
import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'board' / 'bringup'))


def driver():
    assert (ROOT / 'board/bringup/sync_capture.py').is_file(), 'missing sync capture driver'
    return importlib.import_module('sync_capture')


class FIFOMMIO:
    def __init__(self, samples=(), pulses=()):
        self.samples = list(samples)
        self.pulses = list(pulses)
        self.enabled = False
        self.epoch = 0
        self.drops = 0
        self.missed = 0
        self.idle = True

    def read(self, offset):
        fixed = {0: 0x48585331, 4: 0x10000, 0x10: 100000000,
                 0x14: 2048, 0x18: 128, 0x1c: self.epoch,
                 0x20: len(self.samples), 0x24: len(self.pulses),
                 0x28: self.drops, 0x2c: 0, 0x70: len(self.samples),
                 0x74: len(self.pulses), 0x78: int(self.idle), 0x7c: self.missed}
        if offset == 0xc:
            return int(self.enabled) | (not self.samples) * 2 | (not self.pulses) * 4 | bool(self.drops) * 8
        if 0x40 <= offset <= 0x54:
            return self.samples[0][(offset - 0x40) // 4] if self.samples else 0
        if 0x60 <= offset <= 0x6c:
            return self.pulses[0][(offset - 0x60) // 4] if self.pulses else 0
        return fixed[offset]

    def write(self, offset, value):
        if offset == 8:
            self.enabled = bool(value & 1)
            if value & 2:
                self.samples.clear()
                self.pulses.clear()
                self.epoch += 1
                self.drops = self.missed = 0
        elif offset == 0x30 and self.samples:
            self.samples.pop(0)
        elif offset == 0x34 and self.pulses:
            self.pulses.pop(0)
        else:
            raise AssertionError('unexpected register write')


class Clock:
    def __init__(self):
        self.value = 0.0
    def __call__(self):
        self.value += 0.0005
        return self.value
    def sleep(self, delay):
        self.value += delay


class AcqBoundary:
    def __init__(self, hw, sample_gap=False, pulses=True, interrupt=False, tail=True):
        self.hw = hw
        self.active = False
        self.started = False
        self.sample_gap = sample_gap
        self.pulses = pulses
        self.interrupt = interrupt
        self.tail = tail
        self.stops = 0
    def wr(self, off, value):
        assert off == 0x4000
        self.active = bool(value & 1)
        if self.active:
            self.started = True
            self.hw.idle = False
            self.hw.samples += [(0, 2**32 - 2, 8, 0xffffffff, 2, 256000),
                                (2 if self.sample_gap else 1, 24998, 9, 3, 4, 256000)]
            if self.pulses:
                self.hw.pulses += [(998, 9, 1, 0), (48998, 9, 2, 0)]
            if self.interrupt:
                raise KeyboardInterrupt()
        else:
            self.stops += 1
            if self.started and not self.hw.idle and self.tail:
                assert self.hw.enabled, 'capture disabled before source tail published'
                self.hw.samples += [(2, 49998, 9, 5, 6, 256000)]
            self.hw.idle = True
    def wait_idle(self):
        return True


def run_capture(tmp_path, **kwargs):
    mod = driver()
    hw = FIFOMMIO()
    acq = AcqBoundary(hw, **kwargs)
    clock = Clock()
    meta = mod.capture_sync(acq, mod.SyncCapture(hw), 0.01, tmp_path,
                            configure_sensor=lambda unused: None,
                            monotonic=clock, sleep=clock.sleep)
    return meta, acq, hw


def test_fifo_records_keep_64bit_ticks_and_signed_axes_until_explicit_pop():
    mod = driver()
    hw = FIFOMMIO(samples=[(7, 0xfffffffe, 0xffffffff, 0xffffffff, 2, 256000)],
                  pulses=[(0xfffffff0, 0xffffffff, 0xffffffff, 2)])
    sync = mod.SyncCapture(hw)
    assert sync.pop_sample() == (7, 2**64 - 2, -1, 2, 256000)
    assert sync.pop_pulse() == (3 * 2**32 - 1, 2**64 - 16)
    assert sync.pop_sample() is None


def test_capture_stops_source_before_capture_and_includes_inflight_tail(tmp_path):
    meta, acq, hw = run_capture(tmp_path)
    assert meta['status'] == 'PASS'
    assert meta['sample_count'] == 3
    assert meta['pulse_count'] == 2
    assert meta['completed'] is True
    assert not acq.active and not hw.enabled
    assert '2,38654755662,5,6,256000' in (tmp_path / 'samples.csv').read_text()


def test_gap_is_failed_and_partial_data_is_kept(tmp_path):
    meta, acq, hw = run_capture(tmp_path, sample_gap=True, tail=False)
    assert meta['status'] == 'FAILED'
    assert meta['completed'] is False
    assert any('frame' in message for message in meta['errors'])
    assert (tmp_path / 'capture.json').is_file()
    assert not acq.active and not hw.enabled


def test_keyboard_interrupt_stops_and_preserves_partial_records(tmp_path):
    meta, acq, hw = run_capture(tmp_path, interrupt=True)
    assert meta['status'] == 'PARTIAL'
    assert meta['sample_count'] == 3
    assert not acq.active and not hw.enabled


def test_no_pulses_never_passes_rotation_by_default(tmp_path):
    meta, _, _ = run_capture(tmp_path, pulses=False)
    assert meta['status'] == 'FAILED'
    assert meta['rotation_ready'] is False


def test_static_only_is_explicitly_labeled(tmp_path):
    mod = driver()
    hw = FIFOMMIO()
    clock = Clock()
    meta = mod.capture_sync(AcqBoundary(hw, pulses=False), mod.SyncCapture(hw), .01, tmp_path,
                            allow_no_pulses=True, configure_sensor=lambda unused: None,
                            monotonic=clock, sleep=clock.sleep)
    assert meta['status'] == 'STATIC_ONLY'
    assert meta['completed'] is True and meta['rotation_ready'] is False


@pytest.mark.parametrize('seconds', [0, -1, float('nan'), float('inf'), True])
def test_invalid_duration_rejected_before_hardware_changes(tmp_path, seconds):
    mod = driver()
    hw = FIFOMMIO()
    acq = AcqBoundary(hw)
    with pytest.raises(ValueError):
        mod.capture_sync(acq, mod.SyncCapture(hw), seconds, tmp_path)
    assert acq.stops == 0


def test_acq_reuses_loaded_overlay_without_redownload(monkeypatch):
    import types
    called = []
    fake = types.SimpleNamespace(MMIO=lambda base, size: (base, size),
                                 Overlay=lambda xsa: called.append(xsa))
    monkeypatch.setitem(sys.modules, 'pynq', fake)
    mod = importlib.import_module('adxl355_pl_acq')
    overlay = types.SimpleNamespace(ip_dict={'acq': {'phys_addr': 0x41200000}})
    acq = mod.Acq(overlay=overlay)
    assert acq.base == 0x41200000
    assert called == []

def test_overflow_counters_never_receive_pass_verdict(tmp_path):
    mod = driver()
    hw = FIFOMMIO()
    acq = AcqBoundary(hw)
    original = acq.wr
    def write(off, value):
        original(off, value)
        if value & 1:
            hw.drops = 1
    acq.wr = write
    clock = Clock()
    meta = mod.capture_sync(acq, mod.SyncCapture(hw), .01, tmp_path,
                            configure_sensor=lambda unused: None,
                            monotonic=clock, sleep=clock.sleep)
    assert meta['status'] == 'FAILED'
    assert meta['sample_drops'] == 1
    assert any('sample_drops' in error for error in meta['errors'])


def test_source_idle_timeout_is_failure_and_capture_is_disabled(tmp_path):
    mod = driver()
    hw = FIFOMMIO()
    acq = AcqBoundary(hw, tail=False)
    original = acq.wr
    def write(off, value):
        original(off, value)
        if acq.started and not value:
            hw.idle = False
    acq.wr = write
    clock = Clock()
    meta = mod.capture_sync(acq, mod.SyncCapture(hw), .01, tmp_path, idle_timeout=.003,
                            configure_sensor=lambda unused: None,
                            monotonic=clock, sleep=clock.sleep)
    assert meta['status'] == 'FAILED'
    assert any('timeout' in error for error in meta['errors'])
    assert not hw.enabled


def test_abi_mismatch_rejected_before_any_capture_write():
    mod = driver()
    hw = FIFOMMIO()
    original = hw.read
    hw.read = lambda offset: 0 if offset == 0 else original(offset)
    with pytest.raises(ValueError):
        mod.SyncCapture(hw)


def test_clear_epoch_change_invalidates_record_even_when_counts_contiguous(tmp_path):
    mod = driver()
    hw = FIFOMMIO()
    acq = AcqBoundary(hw)
    original = acq.wr
    def write(off, value):
        original(off, value)
        if value & 1:
            hw.epoch += 1
    acq.wr = write
    clock = Clock()
    meta = mod.capture_sync(acq, mod.SyncCapture(hw), .01, tmp_path,
                            configure_sensor=lambda unused: None,
                            monotonic=clock, sleep=clock.sleep)
    assert meta['status'] == 'FAILED'
    assert any('epoch' in error for error in meta['errors'])


def test_capture_does_not_overwrite_existing_data(tmp_path):
    mod = driver()
    (tmp_path / 'samples.csv').write_text('precious')
    hw = FIFOMMIO()
    acq = AcqBoundary(hw)
    with pytest.raises(ValueError):
        mod.capture_sync(acq, mod.SyncCapture(hw), .01, tmp_path)
    assert (tmp_path / 'samples.csv').read_text() == 'precious'
    assert acq.stops == 0


def test_cli_checks_existing_output_before_loading_overlay(tmp_path, monkeypatch):
    mod = driver()
    (tmp_path / 'capture.json').write_text('{}')
    xsa = tmp_path / 'acq_sync.xsa'
    xsa.write_bytes(b'not downloaded during invalid request')
    def download(unused):
        raise AssertionError('invalid CLI request downloaded an overlay')
    monkeypatch.setattr(mod, 'open_capture', download)
    with pytest.raises(SystemExit) as error:
        mod.main(['--xsa', str(xsa), '--out', str(tmp_path)])
    assert error.value.code == 2

def test_pulses_during_warmup_are_not_rotation_ready(tmp_path):
    mod = driver()
    hw = FIFOMMIO()
    acq = AcqBoundary(hw)
    original = acq.wr
    def write(off, value):
        original(off, value)
        if value & 1:
            hw.pulses = [(100, 8, 1, 0), (200, 8, 2, 0)]
    acq.wr = write
    clock = Clock()
    meta = mod.capture_sync(acq, mod.SyncCapture(hw), .01, tmp_path,
                            configure_sensor=lambda unused: None,
                            monotonic=clock, sleep=clock.sleep)
    assert meta['status'] == 'FAILED'
    assert meta['rotation_ready'] is False


def test_missing_initial_sample_cannot_pass_complete_epoch(tmp_path):
    mod = driver()
    hw = FIFOMMIO()
    acq = AcqBoundary(hw, tail=False)
    original = acq.wr
    def write(off, value):
        original(off, value)
        if value & 1:
            hw.samples = [(record[0] + 1,) + record[1:] for record in hw.samples]
    acq.wr = write
    clock = Clock()
    meta = mod.capture_sync(acq, mod.SyncCapture(hw), .01, tmp_path,
                            configure_sensor=lambda unused: None,
                            monotonic=clock, sleep=clock.sleep)
    assert meta['status'] == 'FAILED'
    assert any('initial frame' in error for error in meta['errors'])

def test_pulse_read_interrupt_does_not_discard_already_popped_sample(tmp_path):
    mod = driver()
    hw = FIFOMMIO()
    sync = mod.SyncCapture(hw)
    original = sync.pop_pulse
    interrupted = [False]
    def pop():
        if not interrupted[0]:
            interrupted[0] = True
            raise KeyboardInterrupt()
        return original()
    sync.pop_pulse = pop
    clock = Clock()
    meta = mod.capture_sync(AcqBoundary(hw), sync, .01, tmp_path,
                            configure_sensor=lambda unused: None,
                            monotonic=clock, sleep=clock.sleep)
    assert meta['status'] == 'PARTIAL'
    assert meta['sample_count'] == 3
    assert (tmp_path / 'samples.csv').read_text().splitlines()[1].startswith('0,')
