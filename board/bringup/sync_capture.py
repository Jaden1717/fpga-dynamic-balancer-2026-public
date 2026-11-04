"""Drain the shared-clock ADXL355/tacho FIFOs without reconstructing sample time.

Run with PYNQ 3.0.1 on the board:
  sudo env HOME=/home/xilinx PATH="$PATH" python3 sync_capture.py \
      --xsa acq_sync.xsa --seconds 5 --out capture_sync
Use --allow-no-pulses only for a static acquisition check. STATIC_ONLY is never
rotation evidence. Stop the ADXL source first, drain publication, then freeze
capture. Each FIFO head stays stable until its explicit POP write.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import time

from adxl355_pl_acq import Acq, configure, OFF_CTRL

MAGIC = 0x48585331
VERSION = 0x00010000
MOD64 = 1 << 64


def _signed32(value):
    value = int(value) & 0xffffffff
    return value - (1 << 32) if value & 0x80000000 else value


class SyncCapture:
    """ABI v1 register access; PYNQ MMIO is injected as the hardware boundary."""
    def __init__(self, mmio):
        self.mmio = mmio
        if self.rd(0) != MAGIC or self.rd(4) != VERSION:
            raise ValueError('wrong synchronized-capture magic or ABI version')
        self.clock_hz = self.rd(0x10)
        self.sample_depth = self.rd(0x14)
        self.pulse_depth = self.rd(0x18)
        if not self.clock_hz or not self.sample_depth or not self.pulse_depth:
            raise ValueError('capture clock and FIFO depths must be positive')

    def rd(self, offset):
        return int(self.mmio.read(offset)) & 0xffffffff

    def enable(self, value):
        self.mmio.write(8, int(bool(value)))

    def clear(self):
        self.mmio.write(8, 2)  # clear while disabled; also clears source/tacho counters

    def pop_sample(self):
        if not self.rd(0x20):
            return None
        words = [self.rd(0x40 + 4 * i) for i in range(6)]
        record = (words[0], words[1] | (words[2] << 32),
                  _signed32(words[3]), _signed32(words[4]), _signed32(words[5]))
        self.mmio.write(0x30, 1)
        return record

    def pop_pulse(self):
        if not self.rd(0x24):
            return None
        words = [self.rd(0x60 + 4 * i) for i in range(4)]
        record = (words[2] | (words[3] << 32), words[0] | (words[1] << 32))
        self.mmio.write(0x34, 1)
        return record

    def diagnostics(self):
        return dict(epoch=self.rd(0x1c), sample_drops=self.rd(0x28),
                    pulse_drops=self.rd(0x2c), source_missed=self.rd(0x7c),
                    sample_highwater=self.rd(0x70), pulse_highwater=self.rd(0x74),
                    hardware_status=self.rd(0xc), source_idle=bool(self.rd(0x78) & 1))


def _validate_records(samples, pulses):
    errors = []
    if len(samples) < 2:
        errors.append('fewer than two acceleration samples')
    if samples and samples[0][0] != 0:
        errors.append('missing initial frame 0 of cleared epoch')
    if pulses and pulses[0][0] != 1:
        errors.append('missing initial pulse 1 of cleared epoch')
    for name, records, bits in [('frame', samples, 32), ('pulse', pulses, 64)]:
        modulus = 1 << bits
        for previous, current in zip(records, records[1:]):
            if (current[0] - previous[0]) % modulus != 1:
                errors.append('{} count discontinuity'.format(name))
                break
        for previous, current in zip(records, records[1:]):
            if not 0 < (current[1] - previous[1]) % MOD64 < (1 << 63):
                errors.append('{} ticks are not strictly forward'.format(name))
                break
    if any(not -(1 << 19) <= value < (1 << 19)
           for record in samples for value in record[2:]):
        errors.append('axis value outside signed 20-bit range')
    return errors


def _in_record_pulses(samples, pulses):
    # Subtract integer ticks before any floating conversion (including wrap).
    if len(samples) < 2:
        return 0
    origin = samples[0][1]
    span = sum((b[1] - a[1]) % MOD64 for a, b in zip(samples, samples[1:]))
    count = 0
    for pulse in pulses:
        delta = (pulse[1] - origin) % MOD64
        if delta >= 1 << 63:
            delta -= MOD64
        if 0 <= delta <= span:
            count += 1
    return count


def _save(out, samples, pulses, metadata):
    for name, header, rows in [('samples.csv', ['frame_id', 'ticks', 'x_raw', 'y_raw', 'z_raw'], samples),
                               ('pulses.csv', ['pulse_count', 'ticks'], pulses)]:
        with (out / name).open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
    metadata['raw_sha256'] = {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                              for name in ('samples.csv', 'pulses.csv')}
    (out / 'capture.json').write_text(json.dumps(metadata, indent=2, allow_nan=False) + '\n',
                                      encoding='utf-8')


def capture_sync(acq, sync, seconds, out, allow_no_pulses=False, xsa_sha256=None,
                 batch=64, idle_timeout=1.0, configure_sensor=configure,
                 monotonic=time.monotonic, sleep=time.sleep):
    """Capture to memory, safely stop, then write data plus explicit verdict.

    Runtime failures and Ctrl+C preserve partial records and return failed
    metadata. Invalid arguments / existing output files fail before hardware
    changes. A source-idle timeout is a failure, never a nominal stop.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('seconds must be finite and positive')
    if isinstance(batch, bool) or not isinstance(batch, int) or batch < 1:
        raise ValueError('batch must be a positive integer')
    if not isinstance(idle_timeout, (int, float)) or not math.isfinite(idle_timeout) or idle_timeout <= 0:
        raise ValueError('idle timeout must be finite and positive')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in ('samples.csv', 'pulses.csv', 'capture.json')):
        raise ValueError('output directory already contains capture files; choose a new directory')
    samples, pulses, errors = [], [], []
    meta = dict(schema_version=1, abi_version=VERSION, clock_hz=sync.clock_hz,
                epoch=None, xsa_sha256=xsa_sha256, requested_seconds=float(seconds),
                completed=False, rotation_ready=False, status='FAILED', errors=errors,
                timestamp_semantics='accepted DRDY edge; shared free-running 64-bit clock',
                pulses_per_revolution=1, sample_fifo_depth=sync.sample_depth,
                pulse_fifo_depth=sync.pulse_depth)
    interrupted = False
    started = False
    epoch = None

    def drain():
        taken = 0
        # Bounded alternating queues prevent a continuously busy sample FIFO
        # from starving the lower-rate pulse FIFO or delaying stop checks.
        for _ in range(batch):
            sample = sync.pop_sample()
            if sample is not None:
                samples.append(sample)
                taken += 1
            pulse = sync.pop_pulse()
            if pulse is not None:
                pulses.append(pulse)
                taken += 1
            if sample is None and pulse is None:
                break
        return taken

    def wait_source_idle(drain_records):
        deadline = monotonic() + idle_timeout
        while True:
            if drain_records:
                drain()
            if sync.rd(0x78) & 1:
                return
            if monotonic() > deadline:
                raise RuntimeError('source idle timeout: SPI/publication not drained')
            sleep(.0001)

    try:
        acq.wr(OFF_CTRL, 0)
        wait_source_idle(False)
        acq.wait_idle()
        sync.enable(False)
        meta['sensor_configuration'] = configure_sensor(acq)
        sync.clear()
        epoch = sync.rd(0x1c)
        meta['epoch'] = epoch
        sync.enable(True)
        started = True
        acq.wr(OFF_CTRL, 3)  # source enable + restart; FIFO epoch was cleared first
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            if sync.rd(0x1c) != epoch:
                raise RuntimeError('capture epoch changed during acquisition')
            if not drain():
                sleep(.0001)
    except KeyboardInterrupt:
        interrupted = True
        errors.append('capture interrupted by user')
    except Exception as error:
        errors.append('{}: {}'.format(type(error).__name__, error))
    finally:
        # Even an interrupt between enable and the source's first write comes
        # through this path. Leave capture enabled until the in-flight source
        # transaction and all publication strobes have drained.
        try:
            acq.wr(OFF_CTRL, 0)
            wait_source_idle(started)
        except (Exception, KeyboardInterrupt) as error:
            errors.append('source stop failed: {}'.format(error))
        try:
            sync.enable(False)
            while drain():
                pass
            diag = sync.diagnostics()
            meta.update(diag)
            if epoch is not None and diag['epoch'] != epoch:
                errors.append('capture epoch changed before final drain')
            for key in ('sample_drops', 'pulse_drops', 'source_missed'):
                if diag[key]:
                    errors.append('{}={}'.format(key, diag[key]))
            if diag['hardware_status'] & 0x18:
                errors.append('FIFO overflow sticky status is set')
            if not diag['source_idle']:
                errors.append('source was not idle at final snapshot')
        except (Exception, KeyboardInterrupt) as error:
            errors.append('capture freeze/drain failed: {}'.format(error))
    meta['sensor_configuration'] = getattr(acq, 'sensor_configuration', meta.get('sensor_configuration'))
    meta['driver_sha256'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                             for name in ('sync_capture.py', 'adxl355_pl_acq.py')}
    errors.extend(_validate_records(samples, pulses))
    in_record_pulses = _in_record_pulses(samples, pulses)
    if in_record_pulses < 2 and not allow_no_pulses:
        errors.append('need two optical pulses inside sample time span; static check requires --allow-no-pulses')
    meta.update(sample_count=len(samples), pulse_count=len(pulses),
                in_record_pulse_count=in_record_pulses,
                completed=not errors, rotation_ready=not errors and in_record_pulses >= 2)
    meta['status'] = ('PARTIAL' if interrupted else 'FAILED') if errors else (
        'PASS' if in_record_pulses >= 2 else 'STATIC_ONLY')
    _save(out, samples, pulses, meta)
    return meta


def open_capture(xsa):
    """Download exactly once and resolve the unique capture register block."""
    from pynq import MMIO, Overlay
    overlay = Overlay(str(xsa))
    candidates = [value for name, value in overlay.ip_dict.items()
                  if 'sync_capture_axi' in str(value.get('type', '')) or 'sync_capture_axi' in name]
    if len(candidates) != 1:
        raise RuntimeError('expected one sync_capture_axi in overlay ip_dict, found {}'.format(len(candidates)))
    ip = candidates[0]
    base = ip.get('phys_addr', ip.get('address'))
    if base is None:
        base = ip.get('parameters', {}).get('C_BASEADDR')
    if base is None:
        raise RuntimeError('sync_capture_axi has no physical address')
    base = base if isinstance(base, int) else int(str(base), 0)
    return Acq(overlay=overlay), SyncCapture(MMIO(base, 0x10000))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xsa', default='acq_sync.xsa')
    parser.add_argument('--seconds', type=float, default=5.0)
    parser.add_argument('--out', required=True)
    parser.add_argument('--allow-no-pulses', action='store_true')
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('--seconds must be finite and positive')
    if any((Path(args.out) / name).exists() for name in ('samples.csv', 'pulses.csv', 'capture.json')):
        parser.error('--out already contains capture files; choose a new directory')
    xsa = Path(args.xsa)
    digest = hashlib.sha256(xsa.read_bytes()).hexdigest()
    acq, sync = open_capture(xsa)
    meta = capture_sync(acq, sync, args.seconds, args.out,
                        allow_no_pulses=args.allow_no_pulses, xsa_sha256=digest)
    print('{}: samples={} pulses={} source_missed={} sample_drops={} pulse_drops={}'.format(
        meta['status'], meta['sample_count'], meta['pulse_count'], meta.get('source_missed'),
        meta.get('sample_drops'), meta.get('pulse_drops')))
    for error in meta['errors']:
        print('ERROR: ' + error)
    return 0 if meta['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
