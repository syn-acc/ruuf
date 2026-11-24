"""Test the launcher's sequences and limits with a stand-in for the Colab client.

No Google account, no GPU, no units, no model. The stand-in "model" is handed the answer key by this
test, so it can be right or wrong on purpose; that is the one thing the real GPU machine never gets.
What this proves: the order of calls, the ledger, the limits, what is kept as training data and
what is sent out. What it cannot prove: anything about real training or the real engine.

    RUU_CHILD_HOME=/tmp/some-empty-dir [RUU_CHILD_CODE=/opt/ruu-child] python tests/test_launch.py
"""
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

home = Path(os.environ["RUU_CHILD_HOME"])
code = os.environ.get("RUU_CHILD_CODE", "/opt/ruu-child")
if (home / "child.sqlite3").exists():
    sys.exit("RUU_CHILD_HOME must be a fresh directory: a used one gives false failures")
home.mkdir(parents=True, exist_ok=True)
fake = home / "fake-colab"
fake.write_text(f"""#!{sys.executable}
import json, os, shutil, sys, time
home = {str(home)!r}
remote = os.path.join(home, "remote")
args = sys.argv[1:]
env = dict(a.split("=", 1) for i, a in enumerate(args) if i and args[i - 1] == "--env")
with open(os.path.join(home, "calls.log"), "a") as log:
    log.write(args[0] + (":" + env.get("RUU_DO", "") if args[0] == "exec" else "") + "\\n")
mode = open(os.path.join(home, "mode")).read().strip()
cmd = args[0]
def path(p): return os.path.join(remote, p.lstrip("/"))
def filed(name, folder, files):
    os.makedirs(folder, exist_ok=True)
    for file in files:
        if os.path.exists(path(f"/content/{{name}}.{{file}}")):
            shutil.move(path(f"/content/{{name}}.{{file}}"), os.path.join(folder, file))
    return folder
if cmd == "usage":
    stops = sum(1 for line in open(os.path.join(home, "calls.log")) if line.startswith("stop"))
    print(f"Current balance: {{400.0 - 2.5 * stops}} compute units\\nUsage rate: 0.00/hr\\nActive assignments: 0")
elif cmd == "new":
    sys.exit(3 if mode == "no-gpu" else 0)
elif cmd == "upload":
    os.makedirs(os.path.dirname(path(args[4])), exist_ok=True); shutil.copy(args[3], path(args[4]))
elif cmd == "exec" and env.get("RUU_DO") == "setup":
    print("[launch] engine environment: exit 0 in 1s")
elif cmd == "exec" and env.get("RUU_DO") == "run":
    if mode == "crash":
        print("Traceback: CUDA exploded"); sys.exit(1)
    if mode == "lost-once" and not os.path.exists(os.path.join(home, "lost")):
        open(os.path.join(home, "lost"), "w").close()
        print("RuntimeError: Connection was lost.", flush=True); time.sleep(60); sys.exit(1)
    answers = json.load(open(os.path.join(home, "answers.json")))
    for run_id in env["RUU_JOBS"].split():
        job = filed(run_id, path(f"/content/jobs/{{run_id}}"), ("spec.json", "prompts.jsonl"))
        spec = json.load(open(os.path.join(job, "spec.json")))
        if spec.get("weights") and mode == "no-merged":
            print("ValueError: this engine cannot load the folded weights"); sys.exit(1)
        for folder in (spec.get("lora"), spec.get("weights")):
            if folder and not os.path.isdir(path(folder)):
                print("[worker] tuned weights are not on this machine"); sys.exit(1)
        prompts = [json.loads(l) for l in open(os.path.join(job, "prompts.jsonl")) if l.strip()]
        n = spec["samples"] if spec["temperature"] > 0 else 1
        with open(os.path.join(job, "outputs.jsonl"), "w") as out:
            for j, p in enumerate(prompts):
                for s in range(n):
                    right = (j % 3 != 0) if spec.get("tuned") else ((j + s) % 3 != 0) if n > 1 else (j % 2 == 0)
                    cut = n > 1 and (j + 2 * s) % 11 == 0
                    answer = answers.get(p["id"], "x") if right else "definitely wrong"
                    text = "working " * 3 + ("" if cut else f"<answer>{{answer}}</answer> and some words after it")
                    out.write(json.dumps({{"id": p["id"], "sample": s, "tokens": 1536 if cut else 40 + 7 * ((j * 5 + s * 3) % 6),
                                          "finish": "length" if cut else "stop", "text": text}}) + "\\n")
        json.dump({{"device": "Fake L4", "engine": "fake", "tokens_generated": 1}}, open(os.path.join(job, "meta.json"), "w"))
        print(f"[worker] done {{run_id}}")
    print("[worker] all done")
elif cmd == "exec" and env.get("RUU_DO") == "tune":
    tune = env["RUU_TUNE"]
    src = filed(tune, path(f"/content/tunes/{{tune}}"), ("tune.json", "train.jsonl"))
    rows = [json.loads(l) for l in open(os.path.join(src, "train.jsonl")) if l.strip()]
    out = path(f"/content/tuned/{{tune}}")
    os.makedirs(out + "/adapter", exist_ok=True); os.makedirs(out + "/merged", exist_ok=True)
    head = json.dumps({{"lora_A": {{"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}}}).encode()
    with open(out + "/adapter/adapter_model.safetensors", "wb") as f:
        f.write(len(head).to_bytes(8, "little") + head + b"\\0" * (5 if mode == "short-adapter" else 8))
    json.dump({{"r": 16}}, open(out + "/adapter/adapter_config.json", "w"))
    json.dump({{"steps": len(rows), "device": "Fake L4", "train_seconds": 1.0,
               "loss": {{"before": 2.0, "adapter": 1.0, "merged": 1.01}}}}, open(out + "/tune_meta.json", "w"))
    print("[tune] all done")
elif cmd == "download":
    shutil.copy(path(args[3]), args[4])
elif cmd == "stop":
    pass
""")
fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
env = {**os.environ, "RUU_CHILD_COLAB": str(fake), "PYTHONDONTWRITEBYTECODE": "1", "RUU_CHILD_RETRY_WAIT": "0"}
py = [sys.executable, "-W", "ignore"]
failed = []


def ok(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name, str(extra)[:300] if not cond or extra else "")
    if not cond:
        failed.append(name)


def tool(script, *args):
    return subprocess.run([*py, f"{code}/{script}", *args], capture_output=True, text=True, env=env)


def launch(*args):
    return tool("launch.py", *args)


def last_json(text, key):
    for line in reversed(text.strip().splitlines()):
        if line.startswith("{") and f'"{key}"' in line:
            return json.loads(line)
    return {}


def calls(clear=False):
    log = home / "calls.log"
    out = log.read_text().splitlines() if log.exists() else []
    if clear and log.exists():
        log.unlink()
    return out


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


# ── items: the pool ─────────────────────────────────────────────────────────
built = tool("items.py", "build", "logic-v1")
sys.path.insert(0, code)
import items  # noqa: E402
from common import jsonl, open_db  # noqa: E402

rows = {split: items.load("logic-v1", split) for split in ("dev", "sealed", "pool")}
ok("the pool is built", len(rows["pool"]) > 2000, built.stderr[-200:])
ok("the pool holds seen families only", {r["group"] for r in rows["pool"]} == {"seen"})
tests = {(r["family"], r["sha"]) for split in ("dev", "sealed") for r in rows[split]}
ok("no pool question is a dev or sealed question", not any((r["family"], r["sha"]) in tests for r in rows["pool"]))
live = Path("/data/ruu-child/items/logic-v1")
if (live / "dev.jsonl").exists() and os.access(live / "dev.jsonl", os.R_OK):
    ok("adding the pool left dev and sealed byte-identical to the live sets",
       all(digest(live / f"{s}.jsonl") == digest(home / "items" / "logic-v1" / f"{s}.jsonl") for s in ("dev", "sealed")))

answers = {}
for split in ("dev", "pool"):  # the stand-in model's cheat sheet
    cache = {}
    for r in rows[split]:
        key = (r["family"], r["size"], r["seed"])
        if key not in cache:
            cache[key] = items.dataset(*key)
        with items.quiet():
            answers[r["id"]] = str(cache[key][r["index"]]["answer"])
(home / "answers.json").write_text(json.dumps(answers))
(home / "mode").write_text("ok")

# ── a plain measurement ─────────────────────────────────────────────────────
MODEL = ["--model", "Qwen/Qwen3.5-4B", "--style", "concise", "--max-new-tokens", "1536"]
parent = last_json(launch("prepare", *MODEL, "--per-family", "2").stdout, "run")["run"]
r = launch("colab", parent, "--gpu", "L4")
report = last_json(r.stdout, "session")
seq = [c for c in calls(clear=True) if c != "usage"]
ok("a measurement is: new, 3 uploads, setup, 2 uploads, run, 2 downloads, stop",
   seq == ["new", "upload", "upload", "upload", "exec:setup", "upload", "upload", "exec:run", "download", "download", "stop"], seq)
ok("job done and every item scored", report.get("job") == "done" and report["scored"][0]["status"] == "scored"
   and report["scored"][0]["items_answered"] == 88, r.stdout[-300:] + r.stderr[-300:])
ok("units read from the balance before and after", abs(json.loads(launch("spent").stdout)["units_last_7_days"] - 2.5) < 1e-6)

(home / "mode").write_text("crash")
run2 = last_json(launch("prepare", *MODEL, "--per-family", "1").stdout, "run")["run"]
r = launch("colab", run2, "--gpu", "L4")
report = last_json(r.stdout, "session")
ok("a crashed worker is reported as failed", r.returncode == 1 and report.get("job") == "failed" and "exploded" in report.get("note", ""), report)
ok("the session is still stopped after a crash", "stop" in calls(clear=True))
(home / "mode").write_text("no-gpu")
r = launch("colab", run2, "--gpu", "L4")
ok("no GPU available: failed, stop still attempted", r.returncode == 1 and "stop" in calls())
ok("a GPU outside the allow-list is refused before anything starts",
   launch("colab", run2, "--gpu", "H100").returncode != 0 and calls(clear=True).count("new") == 1)

(home / "mode").write_text("lost-once")
r = launch("colab", run2, "--gpu", "L4")
seq = calls(clear=True)
ok("a dropped connection is retried and the run still lands",
   last_json(r.stdout, "session").get("job") == "done" and seq.count("exec:run") == 2 and seq.count("new") == 1, r.stdout[-300:])
(home / "mode").write_text("ok")

# ── one training screen ─────────────────────────────────────────────────────
r = launch("generation", "--parent", parent, "--pool", "3", "--samples", "3", "--screen", "2",
           "--policy", "verified-short", "unverified-short", "--gpu", "L4")
report = last_json(r.stdout, "arms")
seq = [c for c in calls(clear=True) if c != "usage"]
ok("a training screen runs in one session", seq.count("new") == 1 and seq.count("stop") == 1 and seq[-1] == "stop", seq)
ok("it is: attempts, then per arm a tune and the child's answers",
   [c for c in seq if c.startswith("exec")] == ["exec:setup", "exec:run", "exec:tune", "exec:run", "exec:tune", "exec:run"], seq)
arms = report.get("arms") or [{}, {}]
ok("both arms were tuned, answered and scored", report.get("job") == "done" and len(arms) == 2
   and all(a.get("scored") == "scored" for a in arms), r.stdout[-400:] + r.stderr[-400:])
ok("the comparison with the parent is printed for each arm", r.stdout.count("minus " + parent) == 2, r.stdout[-300:])
db = open_db()
if report.get("attempts"):
    attempt, treat, control = report["attempts"], arms[0]["tune"], arms[1]["tune"]
    right = {(x["item_id"], x["sample"]) for x in db.execute("select item_id, sample from results where run_id=? and correct=1", (attempt,))}
    prompts = {json.dumps(p["messages"]): p["id"] for p in jsonl(home / "runs" / attempt / "job" / "prompts.jsonl")}
    texts = {(o["id"], o["sample"]): o["text"] for o in jsonl(home / "runs" / attempt / "job" / "outputs.jsonl")}
    kept = jsonl(home / "runs" / treat / "job" / "train.jsonl")
    other = jsonl(home / "runs" / control / "job" / "train.jsonl")
    ok("attempts were sampled on pool items only", all(i.startswith("logic-v1/pool/") for i in prompts.values())
       and db.execute("select count(distinct sample) from results where run_id=?", (attempt,)).fetchone()[0] == 3)

    def source(row):  # which attempt a training row came from
        item = prompts[json.dumps(row["messages"])]
        return [(item, s) for (i, s), text in texts.items() if i == item and text.startswith(row["reply"])]

    ok("every kept reply is an attempt uk scored correct", kept and all(any(s in right for s in source(row)) for row in kept), len(kept))
    ok("a kept reply stops at its answer", all(row["reply"].endswith("</answer>") for row in kept + other))
    ok("a training row is the conversation and the reply, nothing else", all(set(row) == {"messages", "reply"} for row in kept + other))
    ok("the control keeps wrong answers too", any(not any(s in right for s in source(row)) for row in other), len(other))
    a, b = (json.loads(db.execute("select spec from tunes where tune_id=?", (t,)).fetchone()[0])["per_family"] for t in (treat, control))
    ok("the control has no more examples per family than the treatment", all(b.get(f, 0) <= n for f, n in a.items()) and set(b) <= set(a), (a, b))
    sent = jsonl(home / "remote" / "content" / "tunes" / treat / "train.jsonl")
    ok("what reached the GPU machine is that file", sent == kept)
    child = db.execute("select * from runs where run_id=?", (arms[0]["run"],)).fetchone()
    ok("the child is recorded as a child of that tune, at the parent's revision",
       child["adapter"] == treat and child["revision"] == db.execute("select revision from runs where run_id=?", (parent,)).fetchone()[0])
    ok("the tune's own report came back", "with adapter 1.0" in tool("keep.py", "show", treat).stdout)
    ok("training data cannot be made from a dev run", tool("keep.py", "make", parent).returncode != 0)
    ok("a child is never asked the pool", launch("prepare", *MODEL, "--split", "pool", "--tuned", treat).returncode != 0)
    ok("a generation needs a base-model parent", launch("generation", "--parent", arms[0]["run"]).returncode != 0)

ok("the child was served from folded weights", arms[0].get("served") == "merged", arms[0])
(home / "mode").write_text("no-merged")
calls(clear=True)
r = launch("generation", "--parent", parent, "--pool", "2", "--samples", "2", "--screen", "1", "--gpu", "L4")
report = last_json(r.stdout, "arms")
ok("when the engine refuses the folded weights, the adapter is tried", report.get("job") == "done"
   and (report.get("arms") or [{}])[0].get("served") == "lora", r.stdout[-400:])
(home / "mode").write_text("ok")

# ── a screen on chosen families ─────────────────────────────────────────────
two = ["products", "time_intervals"]
ok("an unknown family is refused", launch("prepare", *MODEL, "--family", "no_such_family").returncode != 0)
some = last_json(launch("prepare", *MODEL, "--family", *two, "--per-family", "2").stdout, "run")
ok("a family filter keeps only those families", some.get("prompts") == 4, some)
r = launch("generation", "--parent", parent, "--family", *two, "--pool", "3", "--samples", "4", "--screen", "2",
           "--hard-only", "--gpu", "L4")
report = last_json(r.stdout, "arms")
arm = (report.get("arms") or [{}])[0]


def families(run):
    return {x[0] for x in db.execute("select distinct family from results where run_id=?", (run,))}


ok("a screen on chosen families attempts and tests only those", report.get("job") == "done"
   and families(report.get("attempts")) == set(two) and families(arm.get("run")) == set(two), r.stdout[-300:] + r.stderr[-300:])
made = db.execute("select spec from tunes where tune_id=?", (arm.get("tune"),)).fetchone()
kept_stats = json.loads(made[0])["stats"] if made else {}
ok("with --hard-only every kept reply is to an item failed at least half the time",
   kept_stats.get("examples", 0) > 0 and kept_stats.get("examples") == kept_stats.get("examples_from_hard_items"), kept_stats)

# ── a session that stays up ─────────────────────────────────────────────────
calls(clear=True)
r = launch("open", "--gpu", "L4", "--hours", "1")
warm = last_json(r.stdout, "session")
ok("open makes a session and leaves it up", warm.get("open") is True and "stop" not in calls(), r.stdout[-200:] + r.stderr[-200:])
run3 = last_json(launch("prepare", *MODEL, "--per-family", "1").stdout, "run")["run"]
calls(clear=True)
r = launch("colab", run3, "--session", warm["session"])
seq = calls(clear=True)
ok("a run on an open session starts and stops nothing", last_json(r.stdout, "session").get("job") == "done"
   and "new" not in seq and "stop" not in seq, seq)
ok("sessions lists it", warm["session"] in launch("sessions").stdout)
ok("an unknown session is refused", launch("colab", run3, "--session", "rc-nope").returncode != 0)
if "attempt" in globals():  # keeping a child past its session
    made = json.loads(tool("keep.py", "make", attempt).stdout)["tune"]
    launch("tune", made, "--session", warm["session"])
    r = launch("fetch", made, "--session", warm["session"])
    got = last_json(r.stdout, "kept")
    ok("fetch brings the adapter home and marks the tune kept", got.get("kept") is True and got.get("tensors") == 1
       and db.execute("select status from tunes where tune_id=?", (made,)).fetchone()[0] == "kept"
       and (home / "runs" / made / "adapter" / "adapter_config.json").exists(), r.stdout[-300:] + r.stderr[-300:])
    (home / "mode").write_text("short-adapter")
    short = json.loads(tool("keep.py", "make", attempt).stdout)["tune"]
    launch("tune", short, "--session", warm["session"])
    r = launch("fetch", short, "--session", warm["session"])
    ok("a cut-short adapter file is refused", r.returncode != 0 and last_json(r.stdout, "kept").get("kept") is False
       and db.execute("select status from tunes where tune_id=?", (short,)).fetchone()[0] == "tuned", r.stdout[-300:])
    (home / "mode").write_text("ok")
    seq = calls(clear=True)
    ok("fetching starts and stops nothing", "stop" not in seq and "new" not in seq, seq)
r = launch("close", warm["session"])
ok("close stops it", "stop" in calls(clear=True) and launch("colab", run3, "--session", warm["session"]).returncode != 0)

# ── the ceiling and the reaper ──────────────────────────────────────────────
db.execute("insert into jobs(run_id,session,gpu,started,ended,status,units_before,units_after) values('x','s','L4',?,?, 'done', 100, 42)",
           (time.time() - 3600, time.time() - 1800))
db.commit()
calls(clear=True)
r = launch("colab", run3, "--gpu", "L4")
ok("over the weekly ceiling: refused, nothing started", r.returncode != 0 and "new" not in calls(), r.stderr.strip()[-110:])
ok("a generation over the ceiling starts nothing either",
   launch("generation", "--parent", parent, "--gpu", "L4").returncode != 0 and "new" not in calls())
db.execute("delete from jobs where run_id='x'")
for status in ("running", "open"):
    db.execute("insert into jobs(run_id,session,gpu,started,deadline,status) values('y',?,'L4',?,?,?)",
               ("rc-lost-" + status, time.time() - 5 * 3600, time.time() - 2 * 3600, status))
db.commit()
calls(clear=True)
reaped = json.loads(launch("reap").stdout)["reaped"]
ok("overdue sessions are reaped, an open one included", sorted(reaped) == ["rc-lost-open", "rc-lost-running"] and calls().count("stop") == 2, reaped)
ok("reaping twice does nothing", json.loads(launch("reap").stdout)["reaped"] == [])

print(f"\n{'ALL PASSED' if not failed else str(len(failed)) + ' FAILED: ' + '; '.join(failed)}")
sys.exit(1 if failed else 0)
