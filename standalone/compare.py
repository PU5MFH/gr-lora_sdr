#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed identical CF32 IQ to standalone EPFL-derived and lorarx decoders.
No SDR ownership, raw IQ files, MQTT or companion writes. Fail on queue overrun.
"""
import argparse
from datetime import datetime, timezone
import json
from logging.handlers import RotatingFileHandler
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sample-rate', type=int, required=True)
    p.add_argument('--center-hz', type=int, default=923250000)
    p.add_argument('--frequency-hz', type=int, default=923125000)
    p.add_argument('--lorarx', default='/opt/meshcoresdr/lorarx/bin/lorarx')
    p.add_argument('--epfl', default=str(Path(__file__).parent/'build/meshcore-epfl-rx'))
    p.add_argument('--output-dir', default='/var/log/meshcoresdr/epfl-comparison')
    return p.parse_args(argv)


def main(argv=None):
    a = arguments(argv)
    if a.sample_rate < 125000 or abs(a.frequency_hz-a.center_hz)+40000 >= a.sample_rate/2:
        raise ValueError('channel does not fit inside input IQ')
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    commands = {
        'lorarx': [a.lorarx, '-i', '/dev/stdin', '-r', str(a.sample_rate), '-f', 'f32',
                   '-o', str(a.frequency_hz-a.center_hz), '-v', '-N', '-Q', '-H', '5',
                   '-W', '50', '-b', '6', '-s', '8', '-j', '/dev/stdout'],
        'epfl': [a.epfl, '--sample-rate', str(a.sample_rate), '--center-hz', str(a.center_hz),
                 '--frequency-hz', str(a.frequency_hz)],
    }
    # Total retained logs bounded at ~12 MiB for this comparison directory.
    logs = {}
    for name in commands:
        log = logging.getLogger(name)
        log.setLevel(logging.INFO)
        handler = RotatingFileHandler(out/(name+'.jsonl'), maxBytes=2*1024*1024, backupCount=2)
        handler.setFormatter(logging.Formatter('%(message)s'))
        log.addHandler(handler)
        logs[name] = log
    errors = queue.Queue()
    processes, queues, workers = {}, {}, []

    def send(name):
        try:
            while True:
                data = queues[name].get()
                if data is None:
                    break
                view = memoryview(data)
                while view:
                    n = processes[name].stdin.write(view)
                    if not n:
                        raise BrokenPipeError(name)
                    view = view[n:]
            processes[name].stdin.close()
        except Exception as exc:
            errors.put(exc)

    def receive(name):
        try:
            for line in processes[name].stdout:
                # lorarx may emit diagnostic text along with JSON: retain only
                # structured payload records. Diagnostics inherit stderr.
                if len(line) > 1024*1024:
                    raise RuntimeError('oversized decoder record')
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(record, dict):
                    continue
                record.update(received_at=datetime.now(timezone.utc).isoformat(),
                              comparison_started_at=timestamp,
                              input_sample_rate=a.sample_rate,
                              input_center_hz=a.center_hz,
                              channel_hz=a.frequency_hz)
                logs[name].info(json.dumps(record, separators=(',', ':')))
        except Exception as exc:
            errors.put(exc)

    try:
        for name, cmd in commands.items():
            processes[name] = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
            queues[name] = queue.Queue(maxsize=32)  # 2 MiB backlog per decoder
            logs[name].info(json.dumps({'comparison_started_at': timestamp, 'argv': cmd}))
            for target in (send, receive):
                t = threading.Thread(target=target, args=(name,), daemon=True)
                t.start()
                workers.append(t)
        while True:
            if not errors.empty():
                raise errors.get()
            data = os.read(sys.stdin.fileno(), 65536)
            if not data:
                break
            for name, q in queues.items():
                if processes[name].poll() is not None:
                    raise RuntimeError(name+' exited before IQ EOF')
                try:
                    q.put(data, timeout=2)
                except queue.Full:
                    raise RuntimeError(name+' cannot keep up; comparison aborted without silently dropping IQ')
        for name, q in queues.items():
            q.put(None, timeout=5)
        for proc in processes.values():
            if proc.wait(timeout=30):
                raise RuntimeError('decoder failed')
        for t in workers:
            t.join(timeout=5)
            if t.is_alive():
                raise RuntimeError('decoder log/input worker did not finish')
        if not errors.empty():
            raise errors.get()
    finally:
        for proc in processes.values():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        for log in logs.values():
            for handler in list(log.handlers):
                handler.close()
                log.removeHandler(handler)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'comparison stopped: {exc}', file=sys.stderr)
        sys.exit(1)
