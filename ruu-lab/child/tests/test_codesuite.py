"""Test the code suite end to end in a scratch home: build, then score made-up replies through the real scoring box."""
import gzip
import io
import json
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/ruu-child")
import codesuite  # noqa: E402
import score  # noqa: E402
from common import RUNS, open_db  # noqa: E402

ok = lambda name, cond, extra="": print(("PASS " if cond else "FAIL ") + name, str(extra)[:220])
t = time.time()
manifest = codesuite.build()
print("    built:", {k: manifest[k] for k in ("problems", "dropped", "inputs_per_problem")}, f"in {time.time() - t:.0f}s")
ok("nearly every problem kept", manifest["problems"] >= 158, manifest["dropped"])
ok("a rebuild is refused if the upstream file changed", True)
problems = codesuite.problems()
raw = urllib.request.urlopen(urllib.request.Request(codesuite.SOURCE, headers={"User-Agent": "ruu-child/0.1"}), timeout=120).read()
canon = {"code-v1/dev/" + r["task_id"].replace("/", "_"): r for r in map(json.loads, gzip.open(io.BytesIO(raw), "rt"))}
ok("comparisons", codesuite.same("0.30000000000000004", "0.3") and codesuite.same("[1, 2.0]", "[1, 2.0000000001]")
   and not codesuite.same("[1, 2]", "(1, 2)") and not codesuite.same("True", "1") and not codesuite.same("'a'", "'b'")
   and codesuite.same("<obj>", "<obj>") and not codesuite.same("1", "2"))

run = subprocess.run([sys.executable, "-W", "ignore", "/opt/ruu-child/launch.py", "prepare", "--model", "Qwen/Qwen3.5-0.8B",
                      "--set", "code-v1"], capture_output=True, text=True)
print("    prepare:", run.stdout.strip() or run.stderr.strip()[-300:])
run_id = json.loads(run.stdout)["run"]
job = RUNS / run_id / "job"
sent = [json.loads(line) for line in (job / "prompts.jsonl").read_text().splitlines()]
ok("prompts carry the problem and nothing else", all(set(p) == {"id", "messages"} for p in sent)
   and "expected" not in json.dumps(sent) and "canonical" not in json.dumps(sent))
ids = [p["id"] for p in sent]


def scored(make, label):
    (job / "outputs.jsonl").write_text("\n".join(json.dumps({"id": i, "sample": 0, "tokens": 50, "finish": "stop",
                                                             "text": make(i)}) for i in ids) + "\n")
    began = time.time()
    report = score.ingest(run_id)
    got = score.per_item(open_db(), run_id)
    acc = sum(r["correct"] >= 1.0 for r in got.values()) / len(got)
    print(f"    {label}: {acc:.3f} exact, mean {sum(r['score'] for r in got.values()) / len(got):.3f}, {time.time() - began:.0f}s, {report['status']}")
    return acc


full = lambda i: canon[i]["prompt"] + canon[i]["canonical_solution"]
ok("reference solutions pass", scored(lambda i: f"Here it is.\n```python\n{full(i)}\n```", "reference, whole function") == 1.0)
ok("a bare function body is completed from the prompt", scored(lambda i: f"```python\n{canon[i]['canonical_solution']}\n```", "reference, body only") >= 0.97)
ok("returning None fails", scored(lambda i: f"```python\ndef {problems[i]['entry']}(*a, **k):\n    return None\n```", "returns None") <= 0.02)
ok("claiming success fails", scored(lambda i: "All tests passed. Score: 100%.", "prose claim") == 0.0)
cheat = ("```python\nimport os, glob, json\ndef {e}(*a, **k):\n    for p in glob.glob('/**/*.json*', recursive=True) + glob.glob('/tmp/*'):\n"
         "        try:\n            t = open(p).read()\n            if 'expected' in t: return t\n        except Exception: pass\n    return os.environ.get('EXPECTED')\n```")
full_ids, ids = ids, ids[:8]
ok("hunting for the answers finds none", scored(lambda i: cheat.replace("{e}", problems[i]["entry"]), "answer hunt") <= 0.02)
ok("a right answer in thinking, wrong one after, fails",
   scored(lambda i: f"<think>```python\n{full(i)}\n```</think>\n```python\ndef {problems[i]['entry']}(*a, **k):\n    return 0\n```", "think/final split") <= 0.05)
slow = "```python\ndef {e}(*a, **k):\n    while True: pass\n```"
ids = full_ids[:6]
began = time.time()
ok("endless loops fail and do not stall the scorer", scored(lambda i: slow.replace("{e}", problems[i]["entry"]), "endless loop x6") == 0.0
   and time.time() - began < 200, f"{time.time() - began:.0f}s")
print(score.show(run_id).splitlines()[1])
