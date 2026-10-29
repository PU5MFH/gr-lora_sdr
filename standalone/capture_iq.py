#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CF32-only SoapySDR input for either RSP1 or RTL-SDR; no RF transmit."""
import argparse
import math
import os
import signal
import sys
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--driver', choices=('sdrplay', 'rtlsdr'), required=True)
    p.add_argument('--sample-rate', type=int)
    p.add_argument('--center-hz', type=int, default=923250000)
    p.add_argument('--serial')
    p.add_argument('--seconds', type=float, default=60)
    a = p.parse_args()
    rate = a.sample_rate or (500000 if a.driver == 'sdrplay' else 1024000)
    if a.seconds <= 0:
        p.error('--seconds must be positive')
    if a.driver == 'rtlsdr' and not (225000 < rate <= 300000 or 900000 < rate <= 3200000):
        p.error('RTL2832 does not support this hardware rate (512 kS/s requires software resampling)')
    # Bindings are supplied by Debian; RTL Blog V4 needs the isolated library
    # through LD_LIBRARY_PATH, as documented in README.md.
    import numpy as np
    import SoapySDR as s
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    selection = {'driver': a.driver}
    if a.serial:
        selection['serial'] = a.serial
    dev = s.Device(selection)
    stream = None
    active = False
    samples = 0
    try:
        dev.setSampleRate(s.SOAPY_SDR_RX, 0, rate)
        actual = dev.getSampleRate(s.SOAPY_SDR_RX, 0)
        if not math.isclose(actual, rate, abs_tol=.5, rel_tol=0):
            raise RuntimeError(f'requested {rate}, actual {actual}')
        dev.setAntenna(s.SOAPY_SDR_RX, 0, 'Antenna A' if a.driver == 'sdrplay' else 'RX')
        dev.setFrequency(s.SOAPY_SDR_RX, 0, a.center_hz)
        dev.setGainMode(s.SOAPY_SDR_RX, 0, True)
        if a.driver == 'rtlsdr':
            dev.writeSetting('offset_tune', 'false')  # Blog V4 bias tee OFF
        stream = dev.setupStream(s.SOAPY_SDR_RX, s.SOAPY_SDR_CF32, [0])
        if dev.activateStream(stream) != 0:
            raise RuntimeError('activateStream failed')
        active = True
        buf = np.empty(65536, dtype=np.complex64)
        begin = last = time.monotonic()
        print(f'driver={a.driver} rate={actual} center={a.center_hz} format=CF32 AGC=on', file=sys.stderr)
        while not stopping and time.monotonic()-begin < a.seconds:
            result = dev.readStream(stream, [buf], len(buf), timeoutUs=1000000)
            if result.ret == s.SOAPY_SDR_TIMEOUT:
                if time.monotonic()-last > 15:
                    raise RuntimeError('15 seconds without IQ')
                continue
            # A gap invalidates a controlled comparison; stop instead of silently
            # concatenating disjoint IQ as if it were continuous.
            if result.ret <= 0:
                raise RuntimeError(f'readStream error/gap: {result.ret}')
            last = time.monotonic()
            view = memoryview(buf[:result.ret]).cast('B')
            while view:
                n = os.write(sys.stdout.fileno(), view)
                view = view[n:]
            samples += result.ret
    finally:
        if active:
            dev.deactivateStream(stream)
        if stream is not None:
            dev.closeStream(stream)
        print(f'captured_samples={samples}', file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        sys.exit(1)
