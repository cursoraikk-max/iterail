"""POSIX test gate and independent parent-death/deadline guardian.

The gate cannot exec an approved command until its process group has a ready
guardian. EOF before release exits without running it. This is cooperative
process cleanup, not containment of children that escape their session.
"""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time


def _kill_group(group):
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _guardian(control, ready, cutoff):
    group = None
    data = b''
    try:
        while True:
            remaining = cutoff - time.time()
            if remaining <= 0:
                break
            if not select.select([control], [], [], remaining)[0]:
                break
            chunk = os.read(control, 64)
            if not chunk:
                break
            data += chunk
            if group is None and b'\n' in data:
                group = int(data.split(b'\n', 1)[0])
                os.write(ready, b'R')
                os.close(ready)
                ready = None
    finally:
        if group is not None:
            _kill_group(group)
        os.close(control)
        if ready is not None:
            os.close(ready)


def _gate(gate, error, argv):
    os.set_inheritable(error, False)  # successful exec closes launch-error pipe
    released = os.read(gate, 1)
    os.close(gate)
    if released != b'G':
        os.close(error)
        return 125
    try:
        os.execvpe(argv[0], argv, os.environ)
    except OSError as failure:
        os.write(error, json.dumps({'errno': failure.errno, 'message': failure.strerror,
                                   'filename': failure.filename or argv[0]}).encode())
        os.close(error)
        return 127


class Guardian:
    def __init__(self, process, control):
        self.process, self.control = process, control

    def close(self):
        if self.control is not None:
            os.close(self.control)
            self.control = None
        self.process.wait(timeout=2)


def _open_gate(fd):
    os.write(fd, b'G')


def launch(argv, cwd, env, cutoff):
    """Return a real test process plus guardian, or preserve exec OSError."""
    if any('\0' in arg for arg in argv):
        raise ValueError('embedded null byte')
    script = str(Path(__file__).resolve())
    control_r, control_w = os.pipe()
    ready_r, ready_w = os.pipe()
    gate_r, gate_w = os.pipe()
    error_r, error_w = os.pipe()
    opened = {control_r, control_w, ready_r, ready_w, gate_r, gate_w, error_r, error_w}
    watcher = process = None
    def close(fd):
        if fd in opened:
            os.close(fd)
            opened.remove(fd)
    try:
        watcher = subprocess.Popen([sys.executable, script, 'guardian', str(control_r),
                                    str(ready_w), str(cutoff)], env=env,
                                   pass_fds=(control_r, ready_w), start_new_session=True,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        close(control_r)
        close(ready_w)
        process = subprocess.Popen([sys.executable, script, 'gate', str(gate_r), str(error_w),
                                    json.dumps(argv)], cwd=cwd, env=env,
                                   pass_fds=(gate_r, error_w), start_new_session=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        close(gate_r)
        close(error_w)
        os.write(control_w, str(process.pid).encode() + b'\n')
        remaining = max(0, cutoff - time.time())
        if not select.select([ready_r], [], [], remaining)[0] or os.read(ready_r, 1) != b'R':
            raise OSError('guardian did not acknowledge protected process group')
        close(ready_r)
        _open_gate(gate_w)
        close(gate_w)
        if not select.select([error_r], [], [], max(0, cutoff - time.time()))[0]:
            raise OSError('test exec handshake exceeded deadline')
        error = os.read(error_r, 65536)
        close(error_r)
        if error:
            details = json.loads(error)
            raise OSError(details['errno'], details['message'], details['filename'])
        opened.remove(control_w)
        return process, Guardian(watcher, control_w)
    except BaseException as primary:
        # Parent exceptions use the same gate/guardian cleanup as parent death.
        cleanup_errors = []
        def attempt(label, operation):
            try:
                operation()
            except Exception as error:
                cleanup_errors.append(label + ': ' + type(error).__name__ + ': ' + str(error))
        for fd in list(opened):
            attempt('close-fd', lambda fd=fd: close(fd))
        if process is not None:
            attempt('kill-group', lambda: _kill_group(process.pid))
            attempt('reap-gate', lambda: process.wait(timeout=2))
            for stream in (process.stdout, process.stderr):
                attempt('close-stream', stream.close)
        if watcher is not None:
            attempt('reap-guardian', lambda: watcher.wait(timeout=2))
        if cleanup_errors:
            primary.launch_cleanup_errors = cleanup_errors
        raise


if __name__ == '__main__':
    if sys.argv[1] == 'guardian':
        _guardian(int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]))
    else:
        raise SystemExit(_gate(int(sys.argv[2]), int(sys.argv[3]), json.loads(sys.argv[4])))
