"""The code suite: function problems whose solutions are run in the scoring box and judged on the host.

`code-v1` is HumanEval+ (164 problems, public, old). Every 2026 model has probably seen it, so it
can show a relative change between parent and child and nothing about absolute skill. It is here
because it is small, well tested, and exercises the whole path that stage 2 needs: model-written
code runs in a box that holds no answers, and the comparison is made out here.

    codesuite.py build   # download the problems, compute reference outputs in the box, write the item set
"""
import ast
import gzip
import hashlib
import io
import json
import math
import re
import socket
import sys
import urllib.request

from common import ITEMS, answer_region

SOCKET = "/run/ruu-score.sock"
SOURCE = "https://github.com/evalplus/humanevalplus_release/releases/download/v0.1.10/HumanEvalPlus.jsonl.gz"
MAX_INPUTS, MAX_INPUT_CHARS = 60, 4000
SYSTEM = ("You are completing a Python function. Reply with the complete function, including its signature and "
          "any imports it needs, in a single ```python code block. No explanation.")
BLOCK = re.compile(r"```(?:python|py|Python)?[ \t]*\n(.*?)```", re.S)


def box(code, entry, inputs, per_call=2.0, total=25.0):
    """Run code in the scoring box. Any failure to get an answer is a failure of the code, never a pass."""
    payload = json.dumps({"code": code, "entry": entry, "inputs": inputs, "per_call": per_call, "total": total}).encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(total + 60)
            s.connect(SOCKET)
            try:
                s.sendall(payload)
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass  # the box stopped reading: it has refused the job
            chunks = []
            while True:
                data = s.recv(65536)
                if not data:
                    break
                chunks.append(data)
        return json.loads(b"".join(chunks))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"no answer from the box ({type(exc).__name__})"}


def close(a, b, atol):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) or isinstance(b, float):
            return (math.isnan(a) and math.isnan(b)) or math.isclose(a, b, rel_tol=1e-6, abs_tol=max(atol, 1e-6))
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(close(x, y, atol) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k], atol) for k in a)
    return a == b


def same(got, want, atol=0.0):
    """Two printed values agree. Read as Python literals when possible, so 0.1+0.2 can equal 0.3."""
    if got == want:
        return True
    try:
        return close(ast.literal_eval(got), ast.literal_eval(want), atol)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return False


def candidate(problem, text, thinking=False):
    """The program to run for a model's reply: its last code block, completed from the prompt if needed."""
    reply = answer_region(text, thinking)
    blocks = BLOCK.findall(reply)
    body = blocks[-1] if blocks else reply
    if re.search(rf"^[ \t]*def[ \t]+{re.escape(problem['entry'])}[ \t]*\(", body, re.M):
        imports = "\n".join(line for line in problem["prompt"].splitlines() if re.match(r"(import|from)\s", line))
        return imports + "\n" + body
    return problem["prompt"] + body  # a bare function body: finish the prompt's own signature


def judge(problem, text, thinking=False):
    """(fraction of inputs right, all right, note). Runs the reply in the box; compares out here."""
    code = candidate(problem, text, thinking)
    if not code.strip():
        return 0.0, False, "load error"
    if len(code) > 100_000:
        return 0.0, False, "too long"
    result = box(code, problem["entry"], problem["inputs"], per_call=1.0, total=12.0)  # a stuck reply costs 12 s, not 25
    if not result.get("ok"):
        return 0.0, False, result.get("error", "box error")
    right = sum("r" in got and same(got["r"], want, problem.get("atol") or 0.0)
                for got, want in zip(result["results"], problem["expected"]))
    note = "load error" if result.get("load_error") and not right else ("timed out" if result.get("timed_out") else "")
    return right / len(problem["expected"]), right == len(problem["expected"]), note


def problems():
    with (ITEMS / "code-v1" / "problems.jsonl").open(encoding="utf-8") as handle:
        return {p["id"]: p for p in map(json.loads, handle)}


def build():
    out = ITEMS / "code-v1"
    out.mkdir(parents=True, exist_ok=True)
    raw = urllib.request.urlopen(urllib.request.Request(SOURCE, headers={"User-Agent": "ruu-child/0.1"}), timeout=120).read()
    digest, pin = hashlib.sha256(raw).hexdigest(), out / "pin.json"
    if pin.exists() and json.loads(pin.read_text())["sha256"] != digest:
        raise SystemExit("the problem file changed upstream; refusing to rebuild over an existing set")
    rows, kept, dropped = [], [], []
    for line in gzip.open(io.BytesIO(raw), "rt", encoding="utf-8"):
        if line.strip():
            rows.append(json.loads(line))
    for row in rows:
        inputs = [a for a in row["base_input"] + row["plus_input"] if len(json.dumps(a)) <= MAX_INPUT_CHARS][:MAX_INPUTS]
        ref = box(row["prompt"] + row["canonical_solution"], row["entry_point"], inputs)  # the reference runs in the box too
        usable = [(a, got["r"]) for a, got in zip(inputs, ref.get("results") or []) if "r" in got and len(got["r"]) < 19000]
        if not ref.get("ok") or len(usable) < 5:
            dropped.append(row["task_id"])
            continue
        kept.append({"id": "code-v1/dev/" + row["task_id"].replace("/", "_"), "family": "humaneval_plus",
                     "group": "public", "kind": "code", "entry": row["entry_point"], "prompt": row["prompt"],
                     "atol": row.get("atol") or 0.0, "inputs": [a for a, _ in usable],
                     "expected": [r for _, r in usable], "sha": hashlib.sha256(row["prompt"].encode()).hexdigest()[:16]})
    (out / "problems.jsonl").write_text("\n".join(json.dumps(p) for p in kept) + "\n", encoding="utf-8")
    (out / "problems.jsonl").chmod(0o600)
    public = [{k: p[k] for k in ("id", "family", "group", "kind", "entry", "prompt", "sha")} for p in kept]
    (out / "dev.jsonl").write_text("\n".join(json.dumps(p) for p in public) + "\n", encoding="utf-8")
    (out / "dev.jsonl").chmod(0o600)
    pin.write_text(json.dumps({"source": SOURCE, "sha256": digest}), encoding="utf-8")
    manifest = {"set": "code-v1", "source": SOURCE, "problems": len(kept), "dropped": dropped,
                "inputs_per_problem": [min(len(p["inputs"]) for p in kept), max(len(p["inputs"]) for p in kept)],
                "note": "public and old: relative change only"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def prompts(rows):
    return [{"id": row["id"], "messages": [{"role": "system", "content": SYSTEM},
                                           {"role": "user", "content": row["prompt"]}]} for row in rows]


if __name__ == "__main__":
    if sys.argv[1:] == ["build"]:
        print(json.dumps(build(), indent=1))
    else:
        sys.exit(__doc__)
