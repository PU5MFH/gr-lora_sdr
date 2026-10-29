#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression: former fixed SFD energy gate loses this frame before FEC."""
import json
import subprocess
import numpy as np
from test_receiver import BIN, fixture

expected, raw = fixture(500000, offset=-125000)
x = np.frombuffer(raw, dtype='<c8').copy()
rng = np.random.default_rng(187)
# Noise is generated over the input sample bandwidth, referenced to nominal
# BW=62.5k. This is a synthetic setting, not a calibrated lorarx SNR reading.
sigma = np.sqrt((500000/62500)*10**(8/10)/2)
x += (rng.normal(size=len(x))+1j*rng.normal(size=len(x)))*sigma
p = subprocess.run([str(BIN), '--sample-rate', '500000'], input=x.astype('<c8').tobytes(),
                   capture_output=True, check=True)
rows = [json.loads(l) for l in p.stdout.splitlines()]
assert any(r.get('crc') == 1 and r.get('payload_hex') == expected.hex() for r in rows), p.stderr
print('PASS seeded weak-signal regression: matching payload and valid CRC')
# Noise-only should not turn this permissive acquisition into accepted packets.
noise = (rng.normal(size=len(x))+1j*rng.normal(size=len(x))).astype('<c8')*sigma
p = subprocess.run([str(BIN), '--sample-rate', '500000'], input=noise.tobytes(),
                   capture_output=True, check=True)
assert not any(json.loads(l).get('crc') == 1 for l in p.stdout.splitlines())
print('PASS same-length seeded noise-only fixture: no CRC-valid packet')
