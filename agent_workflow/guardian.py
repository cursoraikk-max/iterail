"""Separate process supervising a group; parent stdin EOF cancels execution."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time


def supervise(spec):
    started = time.time()
    out = Path(spec['output'])
    result_path = Path(spec['result'])
    p = subprocess.Popen(spec['argv'], cwd=spec['cwd'], env=spec.get('env'),
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         start_new_session=True)
    result_path.with_suffix('.pid').write_text(json.dumps({'pid': p.pid, 'guardian': os.getpid(), 'started': started,
                                                          'call_id':spec.get('call_id')}))
    selector = selectors.DefaultSelector()
    selector.register(p.stdout, selectors.EVENT_READ, 'output')
    selector.register(sys.stdin.buffer, selectors.EVENT_READ, 'parent')
    count = 0
    saved = 0
    sha = hashlib.sha256()
    reason = None
    group_stopped = False
    cleanup_errors = []
    cutoff = min(started + spec['seconds'], spec.get('total_cutoff', started + spec['seconds']))
    profile = spec.get('timeouts')
    mode = spec.get('startup_mode', 'process_spawn')
    if profile is not None:
        if (set(profile) != {'startup_seconds','idle_seconds','max_progress_extensions'}
                or any(type(profile[x]) is not int or profile[x] <= 0 for x in ('startup_seconds','idle_seconds'))
                or type(profile['max_progress_extensions']) is not int
                or not 0 <= profile['max_progress_extensions'] <= 3
                or mode not in ('claim','process_spawn')):
            raise ValueError('invalid timeout profile')
    startup_complete = mode == 'process_spawn'
    startup_deadline = min(cutoff, started + profile['startup_seconds']) if profile else None
    idle_deadline = min(cutoff, started + profile['idle_seconds']) if profile and startup_complete else None
    last_stage, last_seq = (0 if startup_complete else -1), -1
    extensions = 0
    accepted_progress = []
    control = b''
    termination_started = None
    def checkpoint(line):
        nonlocal startup_complete, idle_deadline, last_stage, last_seq, extensions
        if not profile or reason is not None or len(line) > 1024:
            return
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            return
        if (not isinstance(event, dict) or set(event) != {'type','stage','seq'}
                or event['type'] != 'checkpoint' or type(event['stage']) is not int
                or type(event['seq']) is not int or event['seq'] <= last_seq
                or event['stage'] != last_stage + 1 or not 0 <= event['stage'] <= 3):
            return
        now = time.time()
        if now >= cutoff or (not startup_complete and now >= startup_deadline) or (idle_deadline is not None and now >= idle_deadline):
            return
        last_stage, last_seq = event['stage'], event['seq']
        accepted_progress.append({'stage':last_stage,'seq':last_seq})
        if last_stage == 0:
            startup_complete = True
            idle_deadline = min(cutoff, now + profile['idle_seconds'])
        elif extensions < profile['max_progress_extensions']:
            extensions += 1
            idle_deadline = min(cutoff, now + profile['idle_seconds'])
    def kill_group():
        nonlocal group_stopped, termination_started
        if group_stopped:
            return
        if termination_started is None:
            termination_started = time.time()
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            cleanup_errors.append(type(error).__name__ + ': ' + str(error))
            if p.poll() is None:
                try:p.kill()
                except OSError as direct:cleanup_errors.append(type(direct).__name__ + ': ' + str(direct))
        group_stopped = True
    with out.open('wb') as f:
        while selector.get_map():
            now = time.time()
            if reason is None and now >= cutoff:
                reason = 'total_timeout' if profile else 'timeout'
                kill_group()
            elif reason is None and profile and not startup_complete and now >= startup_deadline:
                reason = 'startup_timeout'
                kill_group()
            elif reason is None and profile and idle_deadline is not None and now >= idle_deadline:
                reason = 'idle_timeout'
                kill_group()
            for key, _ in selector.select(.05):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    if key.data == 'parent' and reason is None and p.poll() is None:
                        reason = 'parent_eof'
                        kill_group()
                    continue
                if key.data == 'output':
                    count += len(chunk)
                    sha.update(chunk)
                    kept = chunk[:max(0, spec['bytes'] - saved)]
                    f.write(kept)
                    f.flush()
                    saved += len(kept)
                elif key.data == 'parent':
                    control += chunk
                    if len(control) > 4096:
                        control = b''  # malformed/oversized input cannot renew
                    while b'\n' in control:
                        line, control = control.split(b'\n',1)
                        checkpoint(line)
            if p.poll() is not None:
                # Stop ordinary descendants even when the direct child exits successfully.
                kill_group()
                for key in list(selector.get_map().values()):
                    if key.data == 'parent':
                        selector.unregister(key.fileobj)
            if termination_started is not None and time.time() > termination_started + 2:
                kill_group()
                break
    kill_group()
    try:rc = p.wait(timeout=2)
    except subprocess.TimeoutExpired:
        rc = None
        cleanup_errors.append('direct child not reaped')
    group_confirmed = False
    until = time.monotonic() + .5
    while time.monotonic() < until:
        try:os.killpg(p.pid, 0)
        except ProcessLookupError:
            group_confirmed = True
            break
        except OSError as error:
            cleanup_errors.append(type(error).__name__ + ': ' + str(error))
            break
        time.sleep(.02)
    selector.close()
    p.stdout.close()
    result = {'argv': spec['argv'], 'cwd': spec['cwd'], 'started': started,
              'duration': time.time()-started, 'returncode': rc,
              'exit_code': 124 if reason in ('timeout','startup_timeout','idle_timeout','total_timeout') else (125 if reason or not group_confirmed or cleanup_errors else rc),
              'reason': reason, 'output_bytes': count, 'saved_bytes': saved,
              'truncated': count > saved, 'output_sha256': sha.hexdigest(), 'output': str(out),
              'call_id':spec.get('call_id'), 'child_pid':p.pid,'guardian_pid':os.getpid(),
              'group_termination_confirmed':group_confirmed,'cleanup_errors':cleanup_errors,
              'total_deadline':cutoff,'startup_deadline':startup_deadline,'idle_deadline':idle_deadline,
              'startup_mode':mode,'startup_complete':startup_complete,
              'accepted_progress':accepted_progress,'progress_extensions':extensions}
    temporary = result_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result))
    temporary.replace(result_path)
    return result


if __name__ == '__main__':
    supervise(json.loads(Path(sys.argv[1]).read_text()))
