"""Trusted local source: measure real verification output retention and peak memory."""
import argparse
import base64
from pathlib import Path
import sys
import tempfile
import tracemalloc

from common import configuration, emit, fixture_repo, load_core

SIZE = 4 * 1024 * 1024
CAP = 65536
PEAK_LIMIT = 8 * 1024 * 1024


def check(repo):
    Engine, digest = load_core(repo)
    evidence = {'source_digest': digest(repo), 'written_bytes_per_stream': SIZE,
                'cap_bytes_per_stream': CAP, 'peak_limit_bytes': PEAK_LIMIT}
    with tempfile.TemporaryDirectory(prefix='workflow-output-check-') as tmp:
        root = Path(tmp)
        fixture = fixture_repo(root)
        code = "import os; block=b'x'*8192; [(os.write(1,block),os.write(2,block)) for _ in range(512)]"
        engine = Engine(root / 'state', configuration(fixture, [sys.executable, '-c', code]))
        try:
            task = engine.import_task('output-cap', 'Output cap')['id']
            run = engine.claim('trusted-output-check', task)
            args = {'run_id': run['id'], 'token': run['token']}
            engine.change(**args)
            tracemalloc.start()
            try:
                result = engine.verify(**args)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            command = result['evidence']['results'][0]
            details = {}
            for stream in ('stdout', 'stderr'):
                raw = base64.b64decode(command.get(stream + '_base64', ''), validate=True)
                details[stream] = {'retained_raw_bytes': len(raw),
                                   'retained_base64_chars': len(command.get(stream + '_base64', '')),
                                   'total_bytes': command.get(stream + '_total_bytes'),
                                   'truncated': command.get(stream + '_truncated'),
                                   'retained_prefix_valid': raw == b'x' * min(SIZE, CAP)}
            evidence.update(peak_bytes=peak, exit_code=command['exit_code'],
                            verification_passed=result['passed'], streams=details)
            passed = (result['passed'] and command['exit_code'] == 0 and peak <= PEAK_LIMIT
                      and all(d['retained_raw_bytes'] <= CAP and d['total_bytes'] == SIZE
                              and d['truncated'] is True and d['retained_prefix_valid']
                              for d in details.values()))
        finally:
            engine.db.close()
    return evidence, passed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    args = parser.parse_args()
    try:
        evidence, passed = check(args.repo)
    except Exception as error:
        evidence, passed = {'error': type(error).__name__ + ': ' + str(error)}, False
    return emit('output-cap', 'Bound verification output memory while preserving counters', evidence,
                'Stream-drain stdout/stderr with a default 65536-byte retained prefix per stream; '
                'record exact stdout_total_bytes/stderr_total_bytes and stdout_truncated/stderr_truncated. '
                'Keep raw base64 capped; preserve command exits and timeouts; avoid hidden full-memory capture.', passed)


if __name__ == '__main__':
    raise SystemExit(main())
