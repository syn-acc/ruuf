"""Prepare and run jobs on Colab. Runs on uk as user ruu-child.

This is the only part of the system that can spend Colab compute units, so it is also where the
limits live: every job is checked against allow.json (root-owned) before anything is started,
and a session is always stopped, whatever happened inside it.

    launch.py prepare --model Qwen/Qwen3.5-4B --set logic-v1 --split dev [--per-family 5] [--samples 1]
    launch.py colab RUN_ID... --gpu L4     # one runtime for these runs: run the worker, fetch outputs, stop it, score
    launch.py generation --parent RUN ...  # one training screen in one runtime: attempts, keep, tune, child's answers
    launch.py open --gpu L4 --hours 1      # a runtime that stays up for several steps; the reaper holds its deadline
    launch.py colab RUN_ID... --session NAME | tune TUNE_ID --session NAME | close NAME | sessions
    launch.py fetch TUNE_ID --session NAME # copy a trained adapter to uk and check the file is whole
    launch.py take RUN_ID DIR              # accept outputs.jsonl/meta.json made by worker.py elsewhere, score
    launch.py usage                        # the account's compute-unit balance, as the Colab client prints it
    launch.py spent                        # units this system has used in the last seven days
    launch.py reap                         # stop any session of ours that outlived its deadline

A run's sealed split is refused unless --promotion is given: the sealed set is for the one
measurement that decides whether a generation is put forward, not for looking.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import items
import keep
import score
from common import CODE, HOME, RUNS, allow, new_run_id, open_db

COLAB = os.environ.get("RUU_CHILD_COLAB") or str(HOME / "venv" / "bin" / "colab")  # the override is for tests
REMOTE = "/content/job"
VM_FILES = ("worker.py", "on_vm.py", "tune.py")
TUNED = "/content/tuned"
ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")


def revision_of(model):
    """Pin the exact weights: a model id alone can change under us between parent and child runs."""
    request = urllib.request.Request(f"https://huggingface.co/api/models/{model}", headers={"User-Agent": "ruu-child/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        info = json.load(response)
    if info.get("gated"):
        raise SystemExit(f"{model} is gated; this system holds no model-hub token")
    return info["sha"]


def prepare(args):
    rules, db = allow(), open_db()
    if args.model not in rules["models"]:
        raise SystemExit(f"{args.model} is not in allow.json")
    if args.set not in items.SETS or args.split not in items.splits(args.set):
        raise SystemExit("unknown item set or split")
    if args.split == "sealed" and not args.promotion:
        raise SystemExit("the sealed split is only scored with --promotion")
    if not 1 <= args.samples <= rules["max_samples"] or not 16 <= args.max_new_tokens <= rules["max_new_tokens"]:
        raise SystemExit("samples or max_new_tokens outside allow.json")
    tune = None
    if args.tuned:
        tune = db.execute("select * from tunes where tune_id=?", (args.tuned,)).fetchone()
        if not tune or tune["model"] != args.model:
            raise SystemExit(f"{args.tuned} is not a tune of {args.model}")
        if args.split == "pool":
            raise SystemExit("a tuned child is not asked the pool it was trained from")
    rows = [r for r in items.load(args.set, args.split) if args.group in ("all", r["group"])]
    if args.family:
        unknown = sorted(set(args.family) - {r["family"] for r in rows})
        if unknown:
            raise SystemExit("not a family of this split: " + ", ".join(unknown))
        rows = [r for r in rows if r["family"] in set(args.family)]
    if args.per_family or args.offset:
        seen_so_far, kept = {}, []
        for row in rows:
            seen_so_far[row["family"]] = seen_so_far.get(row["family"], 0) + 1
            if args.offset < seen_so_far[row["family"]] <= args.offset + (args.per_family or len(rows)):
                kept.append(row)
        rows = kept
    if not rows:
        raise SystemExit("no items match")
    run_id = new_run_id(args.model)
    job = RUNS / run_id / "job"
    job.mkdir(parents=True)
    revision = tune["revision"] if tune else revision_of(args.model)  # a child is compared with the weights it came from
    spec = {"run_id": run_id, "model": args.model, "revision": revision, "adapter": None,
            "item_set": args.set, "split": args.split, "families": sorted(set(args.family or [])), "samples": args.samples,
            "max_new_tokens": args.max_new_tokens, "temperature": args.temperature, "top_p": args.top_p,
            "top_k": args.top_k, "seed": args.seed, "batch_size": args.batch_size, "threads": args.threads,
            "engine": args.engine, "style": args.style, "thinking": bool(args.thinking), "presence_penalty": args.presence_penalty,
            "max_model_len": max(8192, args.max_new_tokens + 4096),
            "chat_template_kwargs": {} if args.thinking else {"enable_thinking": False}}
    if tune:  # the folders are made on the VM by tune.py; they exist only in the session that trained them
        spec["tuned"] = args.tuned
        if args.serve == "lora":
            spec.update(lora=f"{TUNED}/{args.tuned}/adapter", lora_rank=json.loads(tune["spec"])["rank"])
        else:
            spec["weights"] = f"{TUNED}/{args.tuned}/merged"
    (job / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    (job / "prompts.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in items.prompts(rows, args.style)) + "\n", encoding="utf-8")
    db.execute("insert into runs(run_id,created,model,revision,adapter,item_set,split,spec) values(?,?,?,?,?,?,?,?)",
               (run_id, time.time(), args.model, spec["revision"], args.tuned, args.set, args.split, json.dumps(spec)))
    db.commit()
    print(json.dumps({"run": run_id, "prompts": len(rows), "revision": spec["revision"][:12], "tuned": args.tuned,
                      "job": str(job)}), flush=True)
    return run_id


# ── Colab ───────────────────────────────────────────────────────────────────
def colab(*args, timeout=600, check=True):
    """One call to the Colab client."""
    env = {**os.environ, "HOME": str(HOME), "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
    try:
        done = subprocess.run([COLAB, *args], capture_output=True, text=True, timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:  # the client can sit silent after a dropped connection (seen 2026-11-20)
        raise ConnectionError(f"no reply from colab {args[0]} in {timeout:.0f}s") from None
    if check and done.returncode != 0:
        raise RuntimeError(f"colab {args[0]} failed ({done.returncode}): {(done.stderr or done.stdout)[-400:]}")
    return done.stdout + done.stderr


def units_balance():
    """The balance `colab usage` reports, or None when it cannot be read. Never guessed."""
    try:
        text = colab("usage", timeout=60, check=False)
    except Exception:  # noqa: BLE001
        return None
    found = re.search(r"balance[^0-9\-]{0,40}(-?[0-9][0-9,]*\.?[0-9]*)", text, re.I)
    return float(found.group(1).replace(",", "")) if found else None


def spent(db, days=7):
    """Units used by our jobs: measured from the balance when both readings exist, else hours x nominal rate."""
    rates, total = allow()["nominal_units_per_hour"], 0.0
    for job in db.execute("select * from jobs where started>?", (time.time() - days * 86400,)):
        if job["units_before"] is not None and job["units_after"] is not None:
            total += max(0.0, job["units_before"] - job["units_after"])
        else:
            hours = ((job["ended"] or time.time()) - job["started"]) / 3600
            total += hours * rates.get(job["gpu"], max(rates.values()))
    return total


def colab_stream(*args, timeout, log_path, expect):
    """A long `colab exec`: its output goes to a log file as it arrives, progress lines to our own output
    (so the journal shows a job moving), and the call is killed at `timeout` whatever it is doing.
    It succeeded only if a line containing `expect` was seen: a notebook kernel's exit code is not trusted."""
    env = {**os.environ, "HOME": str(HOME), "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
    proc = subprocess.Popen([COLAB, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                            stdin=subprocess.DEVNULL)
    tail, seen, lost = collections.deque(maxlen=12), [], []

    def pump():
        with open(log_path, "a", encoding="utf-8") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                if line.strip():
                    tail.append(line.strip()[:200])
                if expect in line:
                    seen.append(True)
                if "Connection was lost" in line:  # the client prints this and then sits there (seen 2026-11-20)
                    lost.append(True)
                    proc.kill()
                if "[worker]" in line or "[launch]" in line or "[tune]" in line:
                    print(line.rstrip()[:240], flush=True)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=max(60, timeout))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError("the step passed the session's time limit") from None
    reader.join(timeout=15)
    if not seen:
        raise (ConnectionError("the connection to the runtime was lost") if lost else
               RuntimeError(f"did not reach '{expect}' (exit {code}): " + " | ".join(tail)[-400:]))


def patiently(step, attempts=3):
    """Run a step that is safe to repeat (setup, the worker, which skips finished prompts, and the
    trainer, which finds a finished tune). A dropped connection gets another go; anything else is a real failure."""
    for attempt in range(1, attempts + 1):
        try:
            return step()
        except ConnectionError:
            if attempt == attempts:
                raise
            print(f"[launch] connection lost, trying again ({attempt}/{attempts - 1})", flush=True)
            time.sleep(float(os.environ.get("RUU_CHILD_RETRY_WAIT", 20)))


class Session:
    """One Colab runtime and its row in the ledger.

        with Session.new(label, gpu) as s:         made here; stopped on the way out, whatever happened
        with Session.new(..., stay=True) as s:     made here and left up (`open`); the reaper holds its deadline
        with Session.attach(name) as s:            opened earlier; left as it was found

    An ordinary failure inside the block does not escape it: it is written to the ledger and to
    `s.status` / `s.note`, so the caller can still score whatever finished.
    """

    def __init__(self, db, rules, name, gpu, deadline, row, owned, stay):
        self.db, self.rules, self.name, self.gpu, self.deadline, self.row = db, rules, name, gpu, deadline, row
        self.owned, self.stay, self.booted = owned, stay, False
        self.finished, self.note, self.status, self.started = [], "", "failed", time.time()
        self.log_dir = RUNS / "sessions" / name
        self.log_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def new(cls, label, gpu, hours=None, stay=False):
        rules, db = allow(), open_db()
        if gpu not in rules["gpus"]:
            raise SystemExit(f"{gpu} is not in allow.json")
        hours = min(float(hours or rules["max_hours"]), rules["max_hours"])
        used, worst = spent(db), hours * rules["nominal_units_per_hour"][gpu]
        if used + worst > rules["units_per_week"]:
            raise SystemExit(f"refused: {used:.1f} units used this week, this session could use {worst:.1f}, "
                             f"ceiling {rules['units_per_week']}")
        name, before, started = "rc-" + label, units_balance(), time.time()
        row = db.execute("insert into jobs(run_id,session,gpu,started,deadline,status,units_before) values(?,?,?,?,?,?,?)",
                         (label[:400], name, gpu, started, started + hours * 3600, "running", before)).lastrowid
        db.commit()
        return cls(db, rules, name, gpu, started + hours * 3600, row, True, stay)

    @classmethod
    def attach(cls, name):
        rules, db = allow(), open_db()
        job = db.execute("select * from jobs where session=? and status='open' order by id desc", (name,)).fetchone()
        if not job:
            raise SystemExit(f"no open session {name}")
        if job["deadline"] - time.time() < 300:
            raise SystemExit(f"{name} is at its deadline; close it and open another")
        return cls(db, rules, name, job["gpu"], job["deadline"], job["id"], False, True)

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, trace):
        if exc is not None:
            self.note += f"{kind.__name__}: {str(exc)[:400]}"
        self.status = "done" if exc is None else "failed"
        leave_up = self.stay and (exc is None or not self.owned)  # a failed `open` is not left running
        if leave_up:
            if self.owned:
                self.db.execute("update jobs set status='open', note=? where id=?", (self.note, self.row))
            elif self.note:
                self.db.execute("update jobs set note=coalesce(note,'') || ? where id=?", (" | " + self.note, self.row))
        else:
            self.stop(self.status)
        self.db.commit()
        return exc is None or isinstance(exc, (Exception, SystemExit))  # Ctrl-C still gets out, after the stop

    def stop(self, status):
        try:
            colab("stop", "-s", self.name, timeout=300, check=False)
        except Exception as exc:  # noqa: BLE001
            self.note += f" | stop failed: {type(exc).__name__}"
            status = self.status = "stop-failed"
        self.db.execute("update jobs set ended=?, status=?, units_after=?, note=? where id=?",
                        (time.time(), status, units_balance(), self.note, self.row))
        self.db.commit()

    def left(self):
        return self.deadline - time.time() - 240  # keep four minutes back for the downloads and the stop

    def boot(self):
        """The machine, our three files, and the engine's environment. Safe to repeat."""
        if self.booted:
            return
        if self.owned:
            colab("new", "-s", self.name, "--gpu", self.gpu, timeout=900)
        for name in VM_FILES:
            patiently(lambda: colab("upload", "-s", self.name, str(CODE / name), f"/content/{name}", timeout=120))
        patiently(lambda: colab_stream(
            "exec", "-s", self.name, "-f", str(CODE / "on_vm.py"), "--timeout", "900", "--env", "RUU_DO=setup",
            "--env", "RUU_PACKAGES=" + " ".join(self.rules.get("engine_packages", ["vllm"])),
            timeout=min(1200, self.left()), log_path=self.log_dir / "setup.log", expect="engine environment: exit 0"))
        self.booted = True

    def run(self, run_ids):
        """Answer prepared runs. Consecutive runs with the same weights share one loaded engine."""
        runs = [self.db.execute("select * from runs where run_id=?", (run_id,)).fetchone() for run_id in run_ids]
        if not all(runs):
            raise SystemExit("unknown run: " + ", ".join(i for i, r in zip(run_ids, runs) if not r))
        self.boot()
        groups = []
        for run in runs:
            spec = json.loads(run["spec"])
            key = (run["model"], run["revision"], spec.get("weights"), spec.get("lora"))
            if groups and groups[-1][0] == key:
                groups[-1][1].append(run["run_id"])
            else:
                groups.append((key, [run["run_id"]]))
        for key, members in groups:
            for run_id in members:  # flat names: the on-VM script files them, so no extra command is needed
                for name in ("spec.json", "prompts.jsonl"):
                    patiently(lambda: colab("upload", "-s", self.name, str(RUNS / run_id / "job" / name),
                                            f"/content/{run_id}.{name}", timeout=300))
            what = key[0] + (" + adapter" if key[3] else " tuned" if key[2] else "")
            print(f"[launch] {what}: {len(members)} run(s) starting on {self.gpu}", flush=True)
            patiently(lambda: colab_stream(
                "exec", "-s", self.name, "-f", str(CODE / "on_vm.py"), "--timeout", "900", "--env", "RUU_DO=run",
                "--env", "RUU_JOBS=" + " ".join(members), timeout=self.left(),
                log_path=RUNS / members[0] / "worker.log", expect="[worker] all done"))
            for run_id in members:
                for name in ("outputs.jsonl", "meta.json"):
                    patiently(lambda: colab("download", "-s", self.name, f"{REMOTE}s/{run_id}/{name}",
                                            str(RUNS / run_id / "job" / name), timeout=600))
                self.finished.append(run_id)

    def tune(self, tune_id):
        """Train one adapter on the VM from a training file made by keep.py. The weights stay on the VM."""
        tune = self.db.execute("select * from tunes where tune_id=?", (tune_id,)).fetchone()
        if not tune:
            raise SystemExit(f"no tune {tune_id}")
        self.boot()
        job = RUNS / tune_id / "job"
        for name in ("tune.json", "train.jsonl"):
            patiently(lambda: colab("upload", "-s", self.name, str(job / name), f"/content/{tune_id}.{name}", timeout=300))
        print(f"[launch] tuning {tune['model']} on {tune['examples']} examples ({tune['policy']})", flush=True)
        patiently(lambda: colab_stream(
            "exec", "-s", self.name, "-f", str(CODE / "on_vm.py"), "--timeout", "900", "--env", "RUU_DO=tune",
            "--env", "RUU_TUNE=" + tune_id, "--env", "RUU_TUNE_PACKAGES=" + " ".join(self.rules.get("tune_packages", ["peft"])),
            timeout=self.left(), log_path=RUNS / tune_id / "tune.log", expect="[tune] all done"))
        patiently(lambda: colab("download", "-s", self.name, f"{TUNED}/{tune_id}/tune_meta.json",
                                str(job / "tune_meta.json"), timeout=300))
        self.db.execute("update tunes set status='tuned', meta=? where tune_id=?",
                        ((job / "tune_meta.json").read_text(encoding="utf-8"), tune_id))
        self.db.commit()

    def fetch(self, tune_id):
        """Bring a trained adapter home, so the child outlives its session. One try per file: this is
        housekeeping at the end of a session, and it must not eat the minutes a measurement needs."""
        tune = self.db.execute("select * from tunes where tune_id=?", (tune_id,)).fetchone()
        if not tune or tune["status"] not in ("tuned", "kept"):
            raise SystemExit(f"{tune_id} is not a trained tune")
        folder = RUNS / tune_id / "adapter"
        folder.mkdir(parents=True, exist_ok=True)
        for name in ADAPTER_FILES:
            colab("download", "-s", self.name, f"{TUNED}/{tune_id}/adapter/{name}", str(folder / name),
                  timeout=max(60, min(420, self.left())))
        kept = adapter_check(folder)
        self.db.execute("update tunes set status='kept' where tune_id=?", (tune_id,))
        self.db.commit()
        return kept

    def report(self, **more):
        return {"session": self.name, "job": self.status, "minutes": round((time.time() - self.started) / 60, 1),
                "note": self.note, **more}


def adapter_check(folder):
    """Is the adapter file whole? Its header says where every tensor ends; the file must be exactly that long."""
    path = folder / "adapter_model.safetensors"
    with open(path, "rb") as handle:
        head = int.from_bytes(handle.read(8), "little")
        if not 2 <= head <= 100_000_000:
            raise RuntimeError("the adapter file does not start like a weights file")
        tensors = {k: v for k, v in json.loads(handle.read(head)).items() if k != "__metadata__"}
        digest = hashlib.sha256()
        handle.seek(0)
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    size, expected = path.stat().st_size, 8 + head + max(v["data_offsets"][1] for v in tensors.values())
    if size != expected:
        raise RuntimeError(f"the adapter file is {size} bytes and should be {expected}")
    json.loads((folder / "adapter_config.json").read_text(encoding="utf-8"))
    return {"tensors": len(tensors), "megabytes": round(size / 1e6, 1), "sha256": digest.hexdigest()[:16]}


def run_colab(run_ids, gpu, attach=None, hours=None):
    """One Colab session for one or more prepared runs: the VM and the model download are paid for once."""
    session = Session.attach(attach) if attach else Session.new(run_ids[0], gpu, hours)
    with session:
        session.run(run_ids)
    print(json.dumps(session.report(scored=[score.ingest(run_id) for run_id in session.finished])))  # scored after the GPU is released
    return session.status == "done"


def generation(args):
    """One screen of one training idea, in one session.

    The base model attempts pool items several times; uk scores the attempts and keeps some; the model is
    tuned on what was kept; the child answers the same test items as --parent, and uk scores those.
    The first --policy is the idea under test. Any further one is a control, cut to the same number
    of examples per family.
    """
    db = open_db()
    parent = db.execute("select * from runs where run_id=?", (args.parent,)).fetchone()
    if not parent or parent["status"] != "scored" or parent["adapter"]:
        raise SystemExit("--parent must be a scored run of the base model on the items the child will be asked")
    if parent["split"] != "dev":
        raise SystemExit("a screen is scored on dev; the sealed split is for promotions")
    was = json.loads(parent["spec"])
    asked = ["--model", parent["model"], "--set", parent["item_set"], "--style", was.get("style", "default"),
             "--max-new-tokens", str(was["max_new_tokens"])] + (["--thinking"] if was.get("thinking") else [])
    asked += ["--family", *args.family] if args.family else []  # the same families are attempted and tested
    attempt = prepare(prepare_args(asked + [
        "--split", "pool", "--per-family", str(args.pool), "--offset", str(args.pool_offset), "--samples", str(args.samples),
        "--temperature", str(args.temperature), "--top-p", str(args.top_p), "--top-k", str(args.top_k), "--seed", str(args.seed)]))
    as_parent = asked + ["--split", "dev", "--per-family", str(args.screen), "--temperature", str(was.get("temperature", 0.0)),
                         "--top-p", str(was.get("top_p", 1.0)), "--top-k", str(was.get("top_k", 0)), "--seed", str(was.get("seed", 0))]
    arms = []
    session = Session.attach(args.session) if args.session else Session.new(attempt, args.gpu, args.hours)
    with session:
        session.run([attempt])
        print(json.dumps(score.ingest(attempt)), flush=True)  # the GPU waits a few seconds while uk checks the attempts
        for policy in args.policy:
            made = keep.make(attempt, policy, like=arms[0]["tune"] if arms else None, hard_only=args.hard_only,
                             seed=args.seed, rank=args.rank, lr=args.lr, epochs=args.epochs)
            arm = {"policy": policy, "tune": made["tune_id"], "run": None}
            arms.append(arm)
            print(keep.show(made["tune_id"]), flush=True)
            session.tune(made["tune_id"])
            print(keep.show(made["tune_id"]).splitlines()[-1], flush=True)
            # Folded weights first: every trained change is in them and the trainer measured what the fold kept.
            # An engine may load an adapter only in the layers it supports, which would be a different child.
            for serve in (["merged", "lora"] if args.serve == "auto" else [args.serve]):
                child = prepare(prepare_args(as_parent + ["--tuned", made["tune_id"], "--serve", serve]))
                try:
                    session.run([child])
                except RuntimeError as exc:  # the engine refused this way of loading the child; a lost connection is not that
                    if serve == "lora" or args.serve != "auto":
                        raise
                    print(f"[launch] the engine did not take the folded weights ({str(exc)[:160]}); trying the adapter", flush=True)
                    continue
                arm.update(run=child, served=serve)
                break
    for arm in arms:
        if arm["run"] in session.finished:
            arm["scored"] = score.ingest(arm["run"])["status"]
    print(json.dumps(session.report(attempts=attempt, arms=arms)))
    for arm in arms:
        if arm.get("scored"):
            print(f"== {arm['policy']} ({arm['tune']}, served as {arm['served']}) against the parent")
            print(score.compare(args.parent, arm["run"]))
    return session.status == "done"


def open_session(gpu, hours):
    """A runtime that stays up between steps, so each screen does not pay for a new machine and a new install."""
    session = Session.new("warm-" + time.strftime("%m%d-%H%M%S", time.gmtime()), gpu, hours, stay=True)
    with session:
        session.boot()
    print(json.dumps(session.report(open=session.status == "done",
                                    stops_at_utc=time.strftime("%H:%M", time.gmtime(session.deadline)))))
    return session.status == "done"


def close_session(name):
    db = open_db()
    job = db.execute("select * from jobs where session=? and status='open' order by id desc", (name,)).fetchone()
    if not job:
        raise SystemExit(f"no open session {name}")
    out = colab("stop", "-s", name, timeout=300, check=False)
    db.execute("update jobs set ended=?, status='done', units_after=?, note=coalesce(note,'') || ? where id=?",
               (time.time(), units_balance(), " | closed: " + out.strip()[-80:], job["id"]))
    db.commit()
    print(json.dumps({"closed": name, "minutes": round((time.time() - job["started"]) / 60, 1)}))


def sessions():
    rows = open_db().execute("select * from jobs where status in ('running','open','stop-failed') order by id").fetchall()
    for job in rows:
        print(json.dumps({"session": job["session"], "status": job["status"], "gpu": job["gpu"],
                          "minutes_up": round((time.time() - job["started"]) / 60, 1),
                          "minutes_left": round((job["deadline"] - time.time()) / 60, 1)}))
    if not rows:
        print("no session of ours is up")


def reap():
    """Belt and braces for the unit budget: a session past its deadline is stopped even if its launcher died."""
    db, stopped = open_db(), []
    for job in db.execute("select * from jobs where status in ('running','open','stop-failed') and deadline<?", (time.time(),)):
        out = colab("stop", "-s", job["session"], timeout=300, check=False)
        db.execute("update jobs set status='reaped', ended=coalesce(ended, ?), units_after=coalesce(units_after, ?), "
                   "note=coalesce(note,'') || ? where id=?",
                   (time.time(), units_balance(), " | reaped: " + out.strip()[-120:], job["id"]))
        stopped.append(job["session"])
    db.commit()
    print(json.dumps({"reaped": stopped}))


def take(run_id, source):
    job = RUNS / run_id / "job"
    for name in ("outputs.jsonl", "meta.json"):
        if os.path.exists(os.path.join(source, name)):
            shutil.copy(os.path.join(source, name), job / name)
    print(json.dumps(score.ingest(run_id)))


def prepare_parser(a):
    a.add_argument("--model", required=True)
    a.add_argument("--set", default="logic-v1")
    a.add_argument("--split", default="dev")
    a.add_argument("--group", default="all", choices=("all", "seen", "held_out", "public"))
    a.add_argument("--family", nargs="+", help="only these families")
    a.add_argument("--per-family", type=int, default=0, help="N items of each family; 0 = all")
    a.add_argument("--offset", type=int, default=0, help="skip the first N items of each family")
    a.add_argument("--samples", type=int, default=1)
    a.add_argument("--max-new-tokens", type=int, default=768)
    a.add_argument("--temperature", type=float, default=0.0)
    a.add_argument("--top-p", type=float, default=1.0)
    a.add_argument("--top-k", type=int, default=0)
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--batch-size", type=int, default=0)
    a.add_argument("--threads", type=int, default=0)
    a.add_argument("--thinking", action="store_true", help="leave the model's thinking mode on")
    a.add_argument("--engine", default="vllm", choices=("vllm", "hf"))
    a.add_argument("--style", default="default", choices=sorted(items.SYSTEMS), help="how the logic items are asked")
    a.add_argument("--presence-penalty", type=float, default=None)
    a.add_argument("--promotion", action="store_true")
    a.add_argument("--tuned", help="answer with a child: a tune id from keep.py, trained earlier in the same session")
    a.add_argument("--serve", default="merged", choices=("merged", "lora"), help="load the child as folded weights or as an adapter")
    return a


def prepare_args(argv):
    return prepare_parser(argparse.ArgumentParser()).parse_args(argv)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    prepare_parser(sub.add_parser("prepare"))
    b = sub.add_parser("colab")
    b.add_argument("run_ids", nargs="+")
    b.add_argument("--gpu", default="L4")
    b.add_argument("--session", help="use a session opened with `open` instead of making one")
    b.add_argument("--hours", type=float, help="stop the session after this long at the latest")
    g = sub.add_parser("generation")
    g.add_argument("--parent", required=True, help="a scored run of the base model; the child is asked its first --screen items per family")
    g.add_argument("--screen", type=int, default=3, help="test items per family; 0 = all the parent answered")
    g.add_argument("--family", nargs="+", help="attempt and test only these families")
    g.add_argument("--pool", type=int, default=8, help="pool items per seen family to attempt")
    g.add_argument("--pool-offset", type=int, default=0, help="skip pool items already used by an earlier generation")
    g.add_argument("--samples", type=int, default=4, help="attempts per pool item")
    g.add_argument("--temperature", type=float, default=0.7)
    g.add_argument("--top-p", type=float, default=0.8)
    g.add_argument("--top-k", type=int, default=20)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--policy", nargs="+", default=["verified-short"], choices=keep.POLICIES)
    g.add_argument("--hard-only", action="store_true", help="keep only items the model gets wrong at least half the time")
    g.add_argument("--rank", type=int)
    g.add_argument("--lr", type=float)
    g.add_argument("--epochs", type=int)
    g.add_argument("--serve", default="auto", choices=("auto", "lora", "merged"))
    g.add_argument("--gpu", default="L4")
    g.add_argument("--session")
    g.add_argument("--hours", type=float, default=1.5)
    o = sub.add_parser("open")
    o.add_argument("--gpu", default="L4")
    o.add_argument("--hours", type=float, default=1.0)
    for name in ("tune", "fetch"):
        t = sub.add_parser(name)
        t.add_argument("tune_id")
        t.add_argument("--session", required=True)
    for name in ("close",):
        sub.add_parser(name).add_argument("name")
    c = sub.add_parser("take")
    c.add_argument("run_id")
    c.add_argument("source")
    for name in ("usage", "spent", "reap", "sessions"):
        sub.add_parser(name)
    args = p.parse_args()
    if args.cmd == "prepare":
        prepare(args)
    elif args.cmd == "colab":
        sys.exit(0 if run_colab(args.run_ids, args.gpu, args.session, args.hours) else 1)
    elif args.cmd == "generation":
        sys.exit(0 if generation(args) else 1)
    elif args.cmd == "open":
        sys.exit(0 if open_session(args.gpu, args.hours) else 1)
    elif args.cmd == "tune":
        session = Session.attach(args.session)
        with session:
            session.tune(args.tune_id)
        print(json.dumps(session.report()))
        print(keep.show(args.tune_id))
        sys.exit(0 if session.status == "done" else 1)
    elif args.cmd == "fetch":
        session, kept = Session.attach(args.session), {}
        with session:
            kept = session.fetch(args.tune_id)
        print(json.dumps(session.report(tune=args.tune_id, kept=bool(kept), **kept)))
        sys.exit(0 if kept else 1)
    elif args.cmd == "close":
        close_session(args.name)
    elif args.cmd == "sessions":
        sessions()
    elif args.cmd == "take":
        take(args.run_id, args.source)
    elif args.cmd == "usage":
        print(colab("usage", timeout=60, check=False).strip())
    elif args.cmd == "spent":
        print(json.dumps({"units_last_7_days": round(spent(open_db()), 2), "ceiling": allow()["units_per_week"]}))
    elif args.cmd == "reap":
        reap()


if __name__ == "__main__":
    main()
