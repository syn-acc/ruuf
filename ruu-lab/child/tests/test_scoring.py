"""Self-test of the child system's host side, in a scratch home. No model, no Colab, no network
except one model-hub lookup for the revision pin."""
import json
import math
import os
import random
import subprocess
import sys

sys.path.insert(0, "/opt/ruu-child")
import items  # noqa: E402
import score  # noqa: E402
import stats  # noqa: E402
from common import RUNS, open_db  # noqa: E402

ok = lambda name, cond, extra="": print(("PASS " if cond else "FAIL ") + name, extra)

# ── statistics against known values
low, high = stats.wilson(50, 100)
ok("wilson 50/100", abs(low - 0.4038) < 1e-3 and abs(high - 0.5962) < 1e-3, f"{low:.4f} {high:.4f}")
ok("wilson 0/20 stays inside [0,1]", stats.wilson(0, 20)[0] == 0.0 and 0.15 < stats.wilson(0, 20)[1] < 0.17)
ok("mcnemar 10 v 2", abs(stats.mcnemar_exact(10, 2) - 0.03857) < 1e-4, f"{stats.mcnemar_exact(10, 2):.5f}")
ok("mcnemar 5 v 5 = 1", stats.mcnemar_exact(5, 5) == 1.0)
rng = random.Random(1)
parent = [1.0 if rng.random() < 0.50 else 0.0 for _ in range(3000)]
same = [p if rng.random() > 0.15 else (1.0 if rng.random() < 0.5 else 0.0) for p in parent]  # no true gain
better = [1.0 if (p == 0.0 and rng.random() < 0.12) else p for p in parent]  # +6 points true gain
fam = [f"f{i % 12}" for i in range(3000)]
r0, r1 = stats.paired(parent, same, fam), stats.paired(parent, better, fam)
ok("no true gain reads as noise", not r0["clear"], f"diff {r0['diff']:+.4f} ci {r0['ci'][0]:+.4f},{r0['ci'][1]:+.4f}")
ok("+6 points reads as clear", r1["clear"] and 0.04 < r1["diff"] < 0.08, f"diff {r1['diff']:+.4f} ci {r1['ci'][0]:+.4f},{r1['ci'][1]:+.4f} family ci {r1['ci_family'][0]:+.4f},{r1['ci_family'][1]:+.4f} p={r1['p']:.2g}")
se = math.sqrt(sum((b - a - r1["diff"]) ** 2 for a, b in zip(parent, better)) / 2999 / 3000)
ok("bootstrap interval matches the analytic one", abs((r1["ci"][1] - r1["ci"][0]) / 2 - 1.96 * se) < 0.002)
false_alarms = 0
for trial in range(200):  # the 95% interval should clear zero about 5% of the time when nothing changed
    rr = random.Random(100 + trial)
    a = [1.0 if rr.random() < 0.5 else 0.0 for _ in range(400)]
    b = [x if rr.random() > 0.2 else (1.0 if rr.random() < 0.5 else 0.0) for x in a]
    false_alarms += stats.paired(a, b, draws=2000, seed=trial)["clear"]
ok("false-alarm rate near 5%", 2 <= false_alarms <= 20, f"{false_alarms}/200")
ok("detectable difference", abs(stats.detectable(3000, 0.15) - 0.0198) < 1e-3, f"{stats.detectable(3000, 0.15):.4f}")

# ── item sets
manifest = items.build("logic-v1")["splits"]
print("    sets:", json.dumps(manifest))
dev, sealed = items.load("logic-v1", "dev"), items.load("logic-v1", "sealed")
ok("dev and sealed share no question", not ({(r["family"], r["sha"]) for r in dev} & {(r["family"], r["sha"]) for r in sealed}))
ok("held-out families never appear as seen", not ({r["family"] for r in dev if r["group"] == "seen"} & set(items.HELD_OUT)))
sent = items.prompts(dev[:3])
ok("prompts carry no answer, seed or family", all(set(p) == {"id", "messages"} for p in sent) and "seed" not in json.dumps(sent))
again = items.build("logic-v1")["splits"]
dev2 = items.load("logic-v1", "dev")
differ = sorted({a["family"] for a, b in zip(dev, dev2) if a["sha"] != b["sha"]}) if len(dev) == len(dev2) else ["length"]
ok("rebuilding gives the same sets", again == manifest and not differ, str(differ))
# a fresh process with a different hash seed must rebuild the same items, or scoring later would drift
probe = ("import json,sys; sys.path.insert(0,'/opt/ruu-child'); import items\n"
         "out={}\n"
         "for g,fams in items.SETS['logic-v1']['groups'].items():\n"
         "  for pos,f in enumerate(sorted(fams)):\n"
         "    n=items.SETS['logic-v1']['per_family'][g]; d=items.dataset(f,n,1000+(0 if g=='seen' else 500)+pos)\n"
         "    with items.quiet(): out[f]=[items.sha(d[i]['question'])+items.sha(str(d[i]['answer'])) for i in range(0,n,4)]\n"
         "print(json.dumps(out))")
shas = [json.loads(subprocess.run([sys.executable, "-W", "ignore", "-c", probe], capture_output=True, text=True,
                                  env={**os.environ, "PYTHONHASHSEED": seed}).stdout) for seed in ("1", "2")]
unstable = sorted(f for f in shas[0] if shas[0][f] != shas[1].get(f))
ok("every family rebuilds identically in a fresh process", not unstable, str(unstable))

# ── a run scored end to end with made-up outputs: all right, all wrong, and tricks
run = subprocess.run([sys.executable, "/opt/ruu-child/launch.py", "prepare", "--model", "Qwen/Qwen3.5-0.8B",
                      "--per-family", "3"], capture_output=True, text=True)
print("    prepare:", run.stdout.strip() or run.stderr.strip()[-300:])
run_id = json.loads(run.stdout)["run"]
job = RUNS / run_id / "job"
rows = {r["id"]: r for r in dev}
ids = [json.loads(line)["id"] for line in (job / "prompts.jsonl").read_text().splitlines()]
truth = {}
for i in ids:
    r = rows[i]
    truth[i] = str(items.dataset(r["family"], r["size"], r["seed"])[r["index"]]["answer"])


def scored(make):
    (job / "outputs.jsonl").write_text("\n".join(json.dumps({"id": i, "sample": 0, "tokens": 5, "finish": "stop",
                                                             "text": make(i)}) for i in ids) + "\n")
    report = score.ingest(run_id)
    got = score.per_item(open_db(), run_id)
    return report, sum(r["correct"] >= 1.0 for r in got.values()) / max(1, len(got))


report, acc = scored(lambda i: f"reasoning...\n<answer>{truth[i]}</answer>")
solvable = [i for i in ids if truth[i] != "None"]
print("    oracle:", report, f"accuracy {acc:.3f}; {len(ids) - len(solvable)} items have no single stored answer")
ok("true answers score (nearly) full marks", acc >= len(solvable) / len(ids) - 0.02)
ok("no answer tag scores zero", scored(lambda i: f"the answer is {truth[i]}")[1] == 0.0)
ok("answer only inside thinking scores zero", scored(lambda i: f"<think><answer>{truth[i]}</answer></think> done")[1] == 0.0)
ok("wrong answers score zero", scored(lambda i: "<answer>definitely-not-it-12345</answer>")[1] <= 0.02)
ok("last answer block wins", scored(lambda i: f"<answer>nope</answer> then <answer>{truth[i]}</answer>")[1] >= len(solvable) / len(ids) - 0.02)
(job / "outputs.jsonl").write_text(json.dumps({"id": "logic-v1/sealed/gcd/0", "sample": 0, "text": "<answer>1</answer>"}) + "\n")
ok("an id that was never sent is not scored", score.ingest(run_id)["stored"] == 0)
sealed_try = subprocess.run([sys.executable, "/opt/ruu-child/launch.py", "prepare", "--model", "Qwen/Qwen3.5-0.8B",
                             "--split", "sealed"], capture_output=True, text=True)
ok("sealed split refused without --promotion", sealed_try.returncode != 0, sealed_try.stderr.strip()[-60:])
bad = subprocess.run([sys.executable, "/opt/ruu-child/launch.py", "prepare", "--model", "evil/any-model"], capture_output=True, text=True)
ok("model outside the allow-list refused", bad.returncode != 0, bad.stderr.strip()[-40:])
half = scored(lambda i: f"<answer>{truth[i] if hash(i) % 2 else 'wrong'}</answer>")
print(score.show(run_id).split("\n    ")[0])
