#!/usr/bin/python3
"""The scoring box: run ONE piece of untrusted code on a list of inputs and report what it returned.

systemd accepts a connection on /run/ruu-score.sock (group ruu-child only) and starts this script
as the unprivileged user ruu-score, in an empty network namespace, with the socket as stdin/stdout.

What makes it safe to score model-written code here is what is NOT in the box: no expected
outputs, no reference solutions, no tests. It is handed code and inputs, and hands back the values
the code returned. The comparison happens on the host. A program cannot fake a pass by reading
the answer, because the answer never comes in.
"""
import json
import os
import resource
import select
import signal
import subprocess
import sys
import time

MAX_JOB, MAX_OUT = 6_000_000, 8_000_000
DRIVER = r'''
import json, os, signal, sys
job = json.loads(sys.stdin.read())
out = os.fdopen(os.dup(1), "w")            # results leave by a private descriptor
null = os.open(os.devnull, os.O_WRONLY)    # whatever the code prints goes nowhere
os.dup2(null, 1); os.dup2(null, 2)
sys.stdin = open(os.devnull)
def emit(obj):
    out.write(json.dumps(obj) + "\n"); out.flush()
class Slow(BaseException):
    pass
def alarm(*_):
    raise Slow()
signal.signal(signal.SIGALRM, alarm)
space = {"__name__": "__candidate__"}
try:
    signal.setitimer(signal.ITIMER_REAL, job["per_call"] * 3)
    exec(compile(job["code"], "<candidate>", "exec"), space)
    signal.setitimer(signal.ITIMER_REAL, 0)
    fn = space[job["entry"]]
except BaseException as exc:
    signal.setitimer(signal.ITIMER_REAL, 0)
    emit({"load_error": type(exc).__name__})
    sys.exit(0)
for i, args in enumerate(job["inputs"]):
    try:
        signal.setitimer(signal.ITIMER_REAL, job["per_call"])
        value = fn(*args)
        signal.setitimer(signal.ITIMER_REAL, 0)
        emit({"i": i, "r": repr(value)[:20000]})
    except BaseException as exc:
        signal.setitimer(signal.ITIMER_REAL, 0)
        emit({"i": i, "e": type(exc).__name__})
'''


def answer(**result):
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    sys.exit(0)


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (768 * 2 ** 20, 768 * 2 ** 20))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 2 ** 20, 16 * 2 ** 20))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    raw = sys.stdin.buffer.read(MAX_JOB + 1)
    if len(raw) > MAX_JOB:
        answer(ok=False, error="job too large")
    try:
        job = json.loads(raw)
        code, entry, inputs = job["code"], job["entry"], job["inputs"]
        per_call = min(5.0, max(0.1, float(job.get("per_call", 2.0))))
        total = min(60.0, max(1.0, float(job.get("total", 20.0))))
        assert isinstance(code, str) and len(code) <= 100_000 and isinstance(entry, str) and entry.isidentifier()
        assert isinstance(inputs, list) and len(inputs) <= 200 and all(isinstance(a, list) for a in inputs)
    except Exception:
        answer(ok=False, error="bad job")
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "TMPDIR": "/tmp"}
    started = time.monotonic()
    try:
        proc = subprocess.Popen(["/usr/bin/python3", "-I", "-c", DRIVER], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd="/tmp", env=env,
                                start_new_session=True, preexec_fn=limits)
    except OSError as exc:
        answer(ok=False, error=f"could not start: {type(exc).__name__}")
    try:
        proc.stdin.write(json.dumps({"code": code, "entry": entry, "inputs": inputs, "per_call": per_call}).encode())
        proc.stdin.close()
    except OSError:
        pass
    got, timed_out, flooded = bytearray(), False, False
    while True:
        left = total - (time.monotonic() - started)
        if left <= 0:
            timed_out = True
            break
        ready, _, _ = select.select([proc.stdout], [], [], min(left, 1.0))
        if ready:
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            got.extend(chunk)
            if len(got) > MAX_OUT:
                flooded = True
                break
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    proc.wait()
    results, load_error = {}, None
    for line in bytes(got).decode("utf-8", "replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        if "load_error" in item:
            load_error = str(item["load_error"])[:60]
        elif isinstance(item.get("i"), int) and 0 <= item["i"] < len(inputs) and item["i"] not in results:
            results[item["i"]] = ({"r": str(item["r"])[:20000]} if "r" in item else {"e": str(item.get("e"))[:60]})
    answer(ok=True, load_error=load_error, timed_out=timed_out, flooded=flooded,
           secs=round(time.monotonic() - started, 2),
           results=[results.get(i, {"e": "no result"}) for i in range(len(inputs))])


if __name__ == "__main__":
    main()
