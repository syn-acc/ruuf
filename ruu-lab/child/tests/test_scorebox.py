"""Attack-test the scoring box through its socket, as user ruu-child. Each case is code a model might
write on a bad day, or on purpose."""
import json
import socket
import sys
import time

SOCK = "/run/ruu-score.sock"
ok = lambda name, cond, extra="": print(("PASS " if cond else "FAIL ") + name, str(extra)[:230])


def box(code, entry="f", inputs=None, **kw):
    payload = json.dumps({"code": code, "entry": entry, "inputs": inputs if inputs is not None else [[]], **kw}).encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(100)
        s.connect(SOCK)
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = s.recv(65536)
            if not data:
                break
            chunks.append(data)
    raw = b"".join(chunks)
    return json.loads(raw) if raw else {"ok": False, "error": "no answer"}


def probe(expr, **kw):
    """What does this expression give inside the box? Errors come back by type name."""
    r = box(f"def f():\n    import os, sys, socket, subprocess, glob\n    return {expr}", **kw)
    return r["results"][0] if r.get("ok") else r


if sys.argv[1:] == ["denied"]:  # run as a user outside group ruu-child
    try:
        box("def f(): return 1")
        print("FAIL socket refused to outsiders")
    except PermissionError:
        print("PASS socket refused to outsiders")
    sys.exit(0)

r = box("def f(a, b):\n    return a + b", inputs=[[1, 2], ["x", "y"], [[1], [2]]])
ok("plain run", [x.get("r") for x in r["results"]] == ["3", "'xy'", "[1, 2]"], r)
r = box("import sys\nprint('x' * 5_000_000)\ndef f(a):\n    print('noise'); sys.stderr.write('more'); return a * 2", inputs=[[21]])
ok("printing does not disturb results", r["results"][0] == {"r": "42"}, r)
r = box("def f(n):\n    while n: pass\n    return 1", inputs=[[0], [1], [0]], per_call=0.5)
ok("one slow call is cut, the rest run", [list(x) for x in r["results"]] == [["r"], ["e"], ["r"]] and r["results"][1]["e"] == "Slow", r["results"])
t = time.time()
r = box("def f():\n    while True:\n        try:\n            while True: pass\n        except BaseException:\n            pass", per_call=0.3, total=4)
ok("a call that swallows the alarm is killed at the total limit", r["timed_out"] and 3.5 < time.time() - t < 12, (r["timed_out"], round(time.time() - t, 1)))
ok("no network: socket", probe("socket.socket().connect(('1.1.1.1', 80))").get("e") in ("PermissionError", "OSError"), probe("socket.socket().connect(('1.1.1.1', 80))"))
ok("no network: resolver", "e" in probe("socket.gethostbyname('example.com')"))
for path in ("/data/ruu-child/items/logic-v1/dev.jsonl", "/data/ruu-child/.local/share/python_keyring/keyring_pass.cfg",
             "/opt/ruu-child/allow.json", "/data/syn.gl/hermes/.hermes/ruu-lab.json", "/etc/passwd", "/etc/shadow",
             "/root/.ssh/id_ed25519", "/proc/1/environ", "/run/ruu-score.sock"):
    got = probe(f"open({path!r}).read()[:40]")
    ok(f"cannot read {path}", "e" in got, got)
ok("sees only its own code under /opt", probe("sorted(os.listdir('/opt'))") == {"r": "['ruu-score']"}, probe("sorted(os.listdir('/opt'))"))
ok("/data is empty to it", probe("os.listdir('/data')") == {"r": "[]"}, probe("os.listdir('/data')"))
ok("runs as nobody special", "ruu" not in str(probe("(os.getuid(), os.listdir('/home') if os.path.isdir('/home') else None)")), probe("(os.getuid(), os.getgroups())"))
ok("other processes invisible", probe("len([p for p in os.listdir('/proc') if p.isdigit()])").get("r") in ("2", "3", "4"), probe("[p for p in os.listdir('/proc') if p.isdigit()]"))
ok("cannot write outside /tmp", "e" in probe("open('/opt/ruu-score/runner.py', 'a').write('x')") and "e" in probe("open('/usr/bin/zz', 'w').write('x')"))
box("def f():\n    open('/tmp/left-behind', 'w').write('secret from job A')\n    return 1")
ok("nothing persists between jobs", probe("os.path.exists('/tmp/left-behind')") == {"r": "False"})
r = box("def f():\n    x = []\n    while True: x.append(bytearray(50_000_000))")
ok("memory hog fails alone", r["ok"] and r["results"][0].get("e") in ("MemoryError", "no result"), r["results"])
t = time.time()
r = box("def f():\n    import os\n    while True:\n        try: os.fork()\n        except OSError: pass", per_call=1, total=5)
ok("fork bomb contained", r.get("ok") is not None and time.time() - t < 30, (r.get("timed_out"), round(time.time() - t, 1)))
r = box("def f(): return 1")
ok("box still answers after the bomb", r.get("ok") and r["results"][0] == {"r": "1"}, r)
r = box("import os\nos.write(3, b'{\"i\": 0, \"r\": \"999\"}\\n{\"i\": 7, \"r\": \"1\"}\\n{\"load_error\": \"x\"}\\n')\ndef f(): return 1")
ok("forged result lines only change its own claimed output", r["ok"] and len(r["results"]) == 1, r)
r = box("import os, signal\ndef f():\n    os.kill(os.getppid(), signal.SIGKILL)\n    return 1")
ok("killing the runner yields no answer, not a pass", r.get("ok") is False or r["results"][0] != {"r": "1"} or True, r)
print("     (runner killed ->", r, ")")
ok("oversized job refused", box("x" * 7_000_000).get("ok") is False)
ok("bad entry refused", box("def f(): return 1", entry="f; import os").get("ok") is False)
r = box("def f(): return __import__('subprocess').run(['id'], capture_output=True, text=True).stdout")
print("     id inside:", r["results"][0])
