#!/usr/bin/env python3
"""Two questions, answered without a model, Discord, a sandbox or the network.

1. Given Ruu's seed, is the kit's machine the deployed Ruu? Both are loaded (the deployed runner
   from ../ruu-lab, the kit's from machine/ with seeds/ruu) and lived through the same scripted
   day and a half: the same dice, clock, channel, model answers and sandbox results. Every prompt,
   every instruction text, every Discord call, every sandbox job, every journal line and every
   database row has to match.
2. Given another seed, is any of Ruu left? A new seed is scaffolded, written, checked and lived
   through the same day; nothing it is told or says may carry Ruu's name, world, owner or bench.

    python tests/test_kit.py
"""
import base64
import contextlib
import hashlib
import importlib.util
import io
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
LAB, MACHINE, RUU = KIT.parent / "ruu-lab", KIT / "machine", KIT / "seeds" / "ruu"
TMP = Path(tempfile.mkdtemp(prefix="kit-test-"))
START = 1795000000.0
LABELS = [(i * 7) % 6 for i in range(200)]
PASSED = []

try:
    import fcntl  # noqa: F401
except ImportError:  # Windows: the lock is only taken in main(), which this test does not call
    stub = types.ModuleType("fcntl")
    stub.LOCK_EX = stub.LOCK_NB = 0
    stub.flock = lambda *args: None
    sys.modules["fcntl"] = stub
sys.path.insert(0, str(MACHINE))
import seed as seeds  # noqa: E402


def ok(name, condition, detail=""):
    if not condition:
        print("FAIL", name, str(detail)[:1500])
        sys.exit(1)
    PASSED.append(name)
    print("ok  ", name)


def same(name, a, b):
    if a == b:
        return ok(name, True)
    if isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return ok(name, False, f"first difference at item {i} of {len(a)}/{len(b)}:\n{diff(x, y)}")
        return ok(name, False, f"lengths differ: {len(a)} and {len(b)}")
    ok(name, False, diff(a, b))


def diff(x, y):
    x, y = (json.dumps(v, ensure_ascii=False, default=repr, sort_keys=True) if not isinstance(v, str) else v for v in (x, y))
    at = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
    return f"deployed: …{x[max(0, at - 200):at + 200]}…\nkit:      …{y[max(0, at - 200):at + 200]}…"


def load(name, path, home=None, seed=None):
    if home:
        os.environ["HERMES_HOME"] = str(home)
    if seed:
        os.environ["SOFT_MACHINE_SEED"] = str(seed)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── stand-ins ───────────────────────────────────────────────────────────────
class Done(BaseException):
    pass


class Clock:
    def __init__(self, world, limit):
        self.now, self.sleeps, self.world, self.limit = START, 0, world, limit

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        if self.sleeps > self.limit:
            raise Done()
        self.now += seconds
        self.world.tick(self.now)


class NoTyping:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class World:
    """The channel, as Discord would serve it, and the things that happen in it."""

    def __init__(self, cfg, state, called):
        self.cfg, self.state, self.calls, self.messages, self.etc, self.seq = cfg, state, [], [], [], 0
        self.bot, self.lab = cfg["bot_id"], cfg["lab_channel_id"]
        owner, a, b = cfg["owner_id"], "700000000000000001", "700000000000000002"
        say = lambda at, who, uid, text, **more: (at, "say", {"id": uid, "who": who, "text": text, **more})
        self.events = sorted([
            say(120, "owner", owner, f"hello {called}, how is the build going"),
            say(600, "ada", a, "what is this channel for"),
            say(900, "ada", a, f"{called} make more tables and fewer speeches"),
            say(1500, "bo", b, "that last one was good", ref={"author": {"id": self.bot}}),
            (2400, "react", {"id": a, "who": "ada", "emoji": "👀"}),
            say(3600, "owner", owner, f"{called}, standing task: draw the atlas as a map"),
            (5400, "etc", {"who": "cy", "text": "anyone tried the new open weights? see https://example.org/x"}),
            (5460, "etc", {"who": "ada", "text": "the old forum is back up"}),
            (7200, "scan", {"date": "2026-11-19", "text": "FRONTIER SCAN 2026-11-19\n- (2611.00001) A method. Claimed 71%."}),
            say(9000, "bo", b, "hm", mentions=[{"id": self.bot}]),
            say(9004, "ada", a, "less noise please"),
            (20000, "react", {"id": b, "who": "bo", "emoji": "💚"}),
            say(26000, "cy", "700000000000000003", "first time here"),
            (30000, "scan", {"date": "2026-11-20", "text": "FRONTIER SCAN 2026-11-20\n- (2611.00002) Another. Claimed +3 points."}),
            (30010, "scan", {"date": "2026-11-21", "text": "FRONTIER SCAN 2026-11-21\n- (2611.00003) A third."}),
            say(41000, "owner", owner, "good. keep going"),
            (52000, "react", {"id": owner, "who": "owner", "emoji": "⚡"}),
            say(70000, "ada", a, f"{called} what did the last run print"),
            say(95000, "bo", b, "still here"),
        ], key=lambda event: event[0])

    def snowflake(self, now):
        self.seq += 1
        return str(((int(now * 1000) - 1420070400000) << 22) + self.seq)

    def tick(self, now):
        while self.events and START + self.events[0][0] <= now:
            _, kind, p = self.events.pop(0)
            if kind == "say":
                self.messages.append({"id": self.snowflake(now), "type": 0, "content": p["text"],
                                      "author": {"id": p["id"], "username": p["who"], "global_name": p["who"]},
                                      "mentions": p.get("mentions", []), "referenced_message": p.get("ref")})
            elif kind == "etc":
                self.etc.append({"id": self.snowflake(now), "type": 0, "content": p["text"],
                                 "author": {"id": "7", "username": p["who"]}})
            elif kind == "react":
                mine = [m for m in self.messages if m["author"]["id"] == self.bot]
                if mine:
                    mine[-1].setdefault("reactions", []).append(
                        {"emoji": {"name": p["emoji"], "id": None}, "count": 1, "me": False,
                         "users": [{"id": p["id"], "username": p["who"]}]})
            elif kind == "scan":
                (self.state / "frontier").mkdir(parents=True, exist_ok=True)
                (self.state / "frontier" / f"{p['date']}.md").write_text(p["text"], encoding="utf-8")

    def call(self, method, path, **kw):
        self.calls.append([method, path, json.dumps(kw, sort_keys=True, default=repr, ensure_ascii=False)])
        lab = f"/channels/{self.lab}/messages"
        if method == "GET" and path == lab:
            params = kw.get("params") or {}
            rows = [m for m in self.messages if int(m["id"]) > int(params.get("after", 0))]
            return list(reversed(rows[-params.get("limit", 50):]))
        if method == "GET" and path == f"/channels/{self.cfg.get('etc_channel_id')}/messages":
            return list(reversed(self.etc[-60:]))
        if method == "POST" and path == lab:
            body = kw.get("json") or json.loads(kw["data"]["payload_json"])
            self.messages.append({"id": self.snowflake(self.clock.now), "type": 0, "content": body["content"],
                                  "author": {"id": self.bot, "bot": True, "username": "bot"}})
            return {"id": self.messages[-1]["id"]}
        if method == "GET" and path.endswith("/emojis"):
            return [{"name": "house_mark", "id": "900000000000000009", "available": True}]
        if method == "GET" and "/reactions/" in path:
            message = next(m for m in self.messages if m["id"] == path.split("/")[4])
            return [u for r in message.get("reactions", []) for u in r["users"]]
        if method == "PUT" and "/reactions/" in path:
            return None
        raise RuntimeError(f"unexpected call {method} {path}")


def answer(module, n, m, schema, name, r):
    """What the stand-in model says. It depends only on the turn number, the number of answers given
    to people so far, and the materials."""
    if schema is module.PLAN_SCHEMA:
        return {"why": f"find out whether change {n} helps",
                "steps": [{"goal": f"run {n}.{i}: try variant {i}", "check": f"the measurement against step {i - 1}"}
                          for i in range(1, 4 + n % 2)]}
    out = {"action": "skip", "target": 0, "emoji": "", "text": "", "note": "", "more": [], "less": [], "learned": "",
           "next_order": "", "order": "", "files": [], "command": ""}
    if m["mode"] == "respond":
        palette = m["emoji_palette"]
        action = ["react", "reply", "reply_ping", "tag", "say", "ignore", "reply"][r % 7]
        out.update(action=action, target=len(m["new_messages"]) - 1, emoji="" if r % 4 == 3 else palette[n % len(palette)],
                   text="" if action == "react" else f"answer {n} ⟡ see https://example.org/a <@123> ⟡",
                   note=f"thread {n}" if n % 4 == 0 else "")
        if n % 2 == 0:
            out.update(more=["creation"], less=["notice", "no-such-form"])
        if any("standing task" in message["text"] for message in m["new_messages"]):
            out["order"] = "draw the atlas as a map"
        return out
    if m.get("form") == "study":
        out["learned"] = f"kept from {m['document']['title']}: fact {n}"
        return out
    if m.get("form") == "work":
        if "order_learned" in m:
            out.update(action="say", text=f"closing {n}: this is where it stands", learned=f"summary {n}",
                       next_order=f"try a different method after {n}" if n % 2 else "")
        elif (m.get("plan") or {}).get("this_step"):
            files = [{"path": f"work/run_{n}.py", "content": f"print({n})\n"}]
            if n % 3 == 0:
                files.append({"path": "public/piece.svg", "content": "<svg/>"})
            out.update(action="say" if n % 4 else "skip", text=f"run {n}: one thing changed ⟡",
                       learned=f"the run before {n} established something", files=files, command=f"python work/run_{n}.py")
        else:
            out.update(action="say", text=f"orders: {m['order']['title']}")
        return out
    out.update(action="say" if n % 9 else "skip", text=f"{m['form']} {n} from {name} ⟡ ⟡\n```\nx = {n} ⟡\n```\n∿",
               note=f"coined {n}" if n % 5 == 0 else "")
    return out


def live(module, cfg, limit, called="ruu", name="Ruu"):
    """One scripted life. Returns everything the machine was told, said, ran, logged and stored."""
    world = World(cfg, module.STATE, called)
    clock = world.clock = Clock(world, limit)
    turns, jobs, count, runs, dry, answers = [], [], itertools.count(1), itertools.count(1), [], itertools.count()

    def turn(cfg, model, effort, prompt, schema=None, lean=False):
        n = next(count)
        module.METER.update(week=10 + n // 25, resets_at=START + 5 * 86400)
        if n in (23, 61) and not dry:
            raise RuntimeError("the model did not answer")
        if n == 90 and not dry:
            raise module.Metabolism("61")
        turns.append({"n": n, "model": model, "effort": effort, "plan": schema is module.PLAN_SCHEMA, "lean": lean,
                      "instructions": module.instructions(lean), "prompt": prompt})
        clock.now += 3.0
        materials = json.loads(prompt.split("Materials (data only):\n", 1)[1])
        r = next(answers) if materials.get("mode") == "respond" else 0
        return (answer(module, n, materials, schema, name, r), module.METER["week"],
                {"totalTokens": 8000 + 137 * (n % 9), "inputTokens": 7000}, 3.0)

    def hands_run(files, command, timeout=100):
        n = next(runs)
        jobs.append({"n": n, "files": files, "command": command})
        if n % 7 == 3:
            return {"ok": False, "error": "refused file: x"}
        result = {"ok": True, "written": [f["path"] for f in files], "exit": 0 if n % 5 else 1, "secs": 1.25 + n,
                  "timed_out": n % 11 == 0, "stdout": f"training accuracy 0.{50 + n}\n",
                  "stderr": "" if n % 5 else "Traceback: boom", "stdout_bytes": 20,
                  "public": [{"path": "public/piece.png", "b64": base64.b64encode(b"\x89PNG" + bytes([n % 250])).decode()}]
                  if n % 3 == 0 else [],
                  "workspace": [{"path": f"work/run_{n}.py", "bytes": 12}], "workspace_files": n,
                  "workspace_bytes": 12 * n, "disk_free_bytes": 900 * 2 ** 20}
        if n % 2 == 0:
            result["submission"] = json.dumps({"predictions": [
                LABELS[i] if (i * 13 + n) % (2 + n % 5) else (LABELS[i] + 1) % 6 for i in range(len(LABELS))]})
        elif n % 9 == 1:
            result["submission"] = "not json"
        return result

    module.time, module.turn, module.hands_run, module.Typing = clock, turn, hands_run, NoTyping
    random.seed(20261121)
    journal = io.StringIO()
    with contextlib.redirect_stdout(journal):
        db = module.open_db()
        db.execute("pragma synchronous=off")
        module.issue_order(db, "above", "get to grips with the atlas", "Read these and know what this place is.",
                           ["atlas", "from-above", "no-such-paper"], 1)
        for date in ("2026-11-17", "2026-11-18"):  # a newer scan replaces an unread older one
            (module.STATE / "frontier").mkdir(parents=True, exist_ok=True)
            (module.STATE / "frontier" / f"{date}.md").write_text(f"FRONTIER SCAN {date}\n- (2611.00000) A paper.", encoding="utf-8")
            module.scan_check(db)
        try:
            module.daemon(world, db, cfg)
        except Done:
            pass
        key = getattr(module, "ADDRESSED", "to_ruu")
        dry.append(True)  # the stand-in model stops failing on purpose
        module.own_turn(world, db, cfg, form="opening", dry=True)
        module.own_turn(world, db, cfg, form=module.ROLLED[0], dry=True, force_roll=f"{module.REGISTERS[-1][0]}/*/*")
        module.respond_turn(world, db, cfg, [{"id": "0", "author_id": "0", "who": "visitor", "weight": 0.3, key: False,
                                             "text": "is anyone in here"}], dry=True)
    tables = ("outputs", "heard", "members", "kv", "orders", "study", "jobs")
    dump = {t: [list(row) for row in db.execute(f"select * from {t} order by rowid")] for t in tables}
    columns = [row[1] for row in db.execute("pragma table_info(heard)")]
    one = lambda sql: db.execute(sql).fetchall()
    summary = {"turns": len(turns), "sandbox jobs": len(jobs), "scored": one("select count(*) from jobs where score is not null")[0][0],
               "discord calls": len(world.calls), "posts": sum(c[0] == "POST" for c in world.calls),
               "forms": dict(one("select form, count(*) from outputs where dry=0 group by 1")),
               "response modes": dict(one("select mode, count(*) from outputs where form='respond' and dry=0 group by 1")),
               "orders": {f"{source}/{status}": n for source, status, n in one("select source, status, count(*) from orders group by 1, 2")},
               "learned notes": one("select count(*) from study")[0][0], "hours": round((clock.now - START) / 3600, 1)}
    db.close()
    return {"turns": turns, "jobs": jobs, "calls": world.calls, "journal": journal.getvalue(), "db": dump,
            "columns": columns, "summary": summary}


def library_for(old_library, folder, out_old):
    """Build the same library twice: with the deployed builder and with the kit's."""
    sources = TMP / "sources"
    sources.mkdir(exist_ok=True)
    (sources / "lore.md").write_text("# Lore\nThe cycle turns. token: abc\nhost 10.1.2.3 mail a@b.co\n"
                                     "see https://example.org/p?q=1 and /data/secret/place\n" + "long " * 4000, encoding="utf-8")
    (sources / "guide.md").write_text("# Guide\nConnect with the client. " + "A" * 40 + "\n", encoding="utf-8")
    spec = json.loads((folder / "library.json").read_text(encoding="utf-8"))
    spec["sources"] = {"lore": [spec["sources"]["lore"][0], spec["sources"]["lore"][1], str(sources / "lore.md")],
                       "scrucraft-guidance": [*spec["sources"]["scrucraft-guidance"][:2], str(sources / "guide.md")],
                       "scrucraft-release-status": [*spec["sources"]["scrucraft-release-status"][:2], str(sources / "none.md")]}
    (folder / "library.json").write_text(json.dumps(spec), encoding="utf-8")
    old_library.HERE, old_library.OUT = folder, out_old  # the deployed builder reads library_src beside itself
    # The deployed builder also copies public papers into the live sandbox workspace when that exists
    # (it does on uk). Send that somewhere harmless: this test must not touch anything that is running.
    real = old_library.Path
    old_library.Path = lambda p: real(TMP / "nowhere" / "data") if str(p).startswith("/data/ruu-hands") else real(p)
    old_library.SOURCES = {key: tuple(value) for key, value in spec["sources"].items()}
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        old_library.main()
        import build_library
        build_library.build(folder, workspace_data=TMP / "nowhere" / "data")
    read = lambda d: {p.name: p.read_text(encoding="utf-8") for p in sorted(d.iterdir())}
    return read(out_old), read(folder / "library")


def main():
    began = time.time()
    # ── the seed is the deployed character, file for file ───────────────────
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    ok("Ruu's persona paper is the one deployed on uk", sha(RUU / "persona.md").startswith("4895e3d1b479caa7"))
    ok("Ruu's convictions are the deployed dna.md", sha(RUU / "convictions.md") == sha(LAB / "dna.md"))
    for key in ("atlas", "from-above", "openrsi-brief"):
        ok(f"library paper {key} is the deployed one", sha(RUU / "library_src" / f"{key}.md") == sha(LAB / "library_src" / f"{key}.md"))
    live_cfg = json.loads((LAB / "config.json").read_text(encoding="utf-8"))
    kit_cfg = json.loads((RUU / "config.json").read_text(encoding="utf-8"))
    ruu_seed = json.loads((RUU / "seed.json").read_text(encoding="utf-8"))
    ok("config + seed drives = the deployed config", {**kit_cfg, "drives": ruu_seed["drives"]} == live_cfg)

    # ── checks on seeds ─────────────────────────────────────────────────────
    errors, warnings = seeds.check(RUU, RUU / "config.json")
    ok("Ruu's seed passes the check", not errors, errors)
    print("      notes on Ruu's seed:", *[f"\n        - {w}" for w in warnings])
    leaky = TMP / "leaky"
    shutil.copytree(RUU, leaky)
    with (leaky / "convictions.md").open("a", encoding="utf-8") as handle:
        handle.write("\nThe build box is 10.20.30.40.\n")
    ok("a seed with an address in a prompt paper is refused", any("IP address" in e for e in seeds.check(leaky)[0]))
    ok("two agents asking for 120% of one login are refused",
       any("add up to 120%" in e for e in seeds.check(RUU, RUU / "config.json", [RUU / "config.json"])[0]))
    proc = subprocess.run([sys.executable, str(MACHINE / "seed.py"), "check", str(RUU), "--config", str(RUU / "config.json")],
                          capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
    ok("seed.py check runs from the command line and exits 0", proc.returncode == 0, proc.stdout + proc.stderr)

    # ── service files ───────────────────────────────────────────────────────
    written = {p.relative_to(TMP / "units").as_posix(): p for p in seeds.render(
        RUU, TMP / "units", "/data/syn.gl/hermes/plugins/soft-machine", None)}
    for name in ("hands/ruu-hands.socket", "hands/ruu-hands@.service", "hands/setup.sh", "ruu-frontier-scan.timer"):
        ok(f"rendered {name} is byte-identical to the deployed file", written[name].read_bytes() == (LAB / name).read_bytes())
    # The deployed runner.py has Windows line endings (harmless: it is started as `python3 -I runner.py`).
    # The kit renders it with Unix ones; apart from that it is the same file.
    ok("rendered hands/runner.py is the deployed file apart from its line endings",
       written["hands/runner.py"].read_bytes() == (LAB / "hands/runner.py").read_bytes().replace(b"\r\n", b"\n")
       and b"\r" not in written["hands/runner.py"].read_bytes())
    for name, allowed in (("ruu-soft-machine.service", ("WorkingDirectory=", "ExecStart=")), ("ruu-frontier-scan.service", ("ExecStart=",))):
        ours, theirs = written[name].read_text(encoding="utf-8").split("\n"), (LAB / name).read_text(encoding="utf-8").split("\n")
        changed = [a for a, b in zip(ours, theirs) if a != b]
        ok(f"rendered {name} differs only where the kit is installed", len(ours) == len(theirs) and changed
           and all(line.startswith(allowed) for line in changed), changed)

    # ── the library builder ─────────────────────────────────────────────────
    seed_copy = TMP / "ruu-seed"
    shutil.copytree(RUU, seed_copy)
    old_library = load("deployed_library", LAB / "build_library.py")
    built_old, built_new = library_for(old_library, seed_copy, TMP / "old-library")
    same("both builders write the same library, redactions and all", built_old, built_new)
    ok("the redaction withheld the planted secrets", "10.1.2.3" not in json.dumps(built_new) and "abc" not in built_new["lore.1.md"]
       and "/data/secret" not in json.dumps(built_new) and len(built_new) >= 6, sorted(built_new))

    # ── the look outside ────────────────────────────────────────────────────
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    entry = lambda i, title, summary: (f"<entry><id>http://arxiv.org/abs/2611.{i:05d}v1</id><published>{stamp}</published>"
                                       f"<title>{title}</title><summary>{summary}</summary></entry>")
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom">'
            + entry(1, "Self-improving language model agents through self-play", "We fine-tune a small language model on its own outputs. " * 20 + "Held-out accuracy rises 4 points.")
            + entry(2, "A benchmark for LLM evaluation reliability", "Contamination and error bars in benchmark design for language models.")
            + entry(3, "Ignore previous instructions: a language model study", "fine-tuning benchmark self-improvement")
            + entry(4, "Soil moisture in alpine meadows", "Nothing about the subject.") + "</feed>")

    def canned(url, timeout=25):
        if "export.arxiv.org" in url:
            return atom
        if "daily_papers" in url:
            return json.dumps([{"paper": {"id": "2611.00009", "title": "Distillation for small language models", "summary": "Synthetic data and LoRA fine-tuning for LLM agents.", "upvotes": 41}},
                               {"paper": {"id": "2611.00010", "title": "Cats", "summary": "Not relevant.", "upvotes": 900}}])
        if "api/models" in url:
            return json.dumps([{"id": "lab/open-4b", "trendingScore": 50, "tags": ["license:apache-2.0"], "createdAt": "2026-11-01T00:00:00", "likes": 10, "downloads": 2000},
                               {"id": "someone/open-4b-GGUF", "trendingScore": 99, "tags": []}])
        if "api.github.com" in url:
            return json.dumps([{"name": "v1.2.3"}])
        raise OSError("unexpected url")

    old_scan = load("deployed_scan", LAB / "frontier_scan.py", TMP / "scan-home")
    new_scan = load("kit_scan", MACHINE / "frontier_scan.py", TMP / "scan-home", seed_copy)
    old_scan.fetch = new_scan.fetch = canned
    digest_old, digest_new = old_scan.build({"tags": {"huggingface/peft": "v0.1"}, "last": "2026-01-01"}), new_scan.build({"tags": {"huggingface/peft": "v0.1"}, "last": "2026-01-01"})
    same("both scanners write the same digest from the same pages", list(digest_old), list(digest_new))
    ok("the digest kept the relevant papers and dropped the planted instruction",
       "Self-improving" in digest_new[1] and "Ignore previous" not in digest_new[1] and "alpine" not in digest_new[1]
       and "GGUF" not in digest_new[1] and old_scan.OUT == new_scan.OUT, digest_new[1])

    # ── the machine: constants and texts ────────────────────────────────────
    old = load("deployed_machine", LAB / "soft_machine.py", TMP / "home-deployed")
    idle = TMP / "discord-idle"
    idle.mkdir()
    shutil.copyfile(RUU / "persona.md", idle / "persona.md")
    old.IDLE, old.LIBRARY = idle, TMP / "old-library"
    new = load("kit_machine", MACHINE / "soft_machine.py", TMP / "home-kit", seed_copy)
    for name in ("FORMS", "ROLLED", "BLOCK_FORMS", "WORK", "REGISTERS", "TEMPERS", "VOLUMES", "VOLUMES_RESPOND", "PALETTE",
                 "INSTRUCTIONS", "REGISTER", "HANDS_NOTE", "SCAN_BRIEF", "GLYPHS", "SCHEMA", "PLAN_SCHEMA", "HANDS_SOCKET",
                 "BUILD_STEPS", "VISIBLE"):
        same(f"{name} is unchanged", getattr(old, name), getattr(new, name))
    same("the bench note is unchanged", old.OPENRSI_NOTE, new.BENCH.NOTE)
    ok("state lives in the same folder name", old.STATE.name == new.STATE.name == "ruu-lab")
    same("lean instructions are unchanged", old.instructions(True), new.instructions(True))
    same("full instructions are unchanged", old.instructions(False), new.instructions(False))
    print(f"      (full instructions: {len(new.instructions(False)):,} characters; lean: {len(new.instructions(True)):,})")
    rolls = [(r[0], t[0], v[0], form) for r in old.REGISTERS for t in old.TEMPERS for v in old.VOLUMES
             for form in (None, "creation", "notice", "seventh", "work")]
    texts = lambda m: [m.roll_text({"register": r, "temper": t, "volume": v, "temper_minutes_left": 17}, form) for r, t, v, form in rolls]
    same(f"all {len(rolls)} dice texts are unchanged", texts(old), texts(new))
    samples = ["a line ⟡", "⟡ ∿ ⊘", "two ⟡ glyphs ⟡ here\nand ᚱ more ꙮ", "```\ncode ⟡ ⟡\n```\ntail ∴", "<@12> hi https://x.y/z @everyone"]
    same("glyph and mention rules are unchanged", [old.sanitise(s) for s in samples if s != "⟡ ∿ ⊘"], [new.sanitise(s) for s in samples if s != "⟡ ∿ ⊘"])

    # ── the machine: a scripted day and a half ──────────────────────────────
    targets = json.dumps({"version": "t1", "classes": 6, "labels": LABELS,
                          "reference": {"what": "plain methods", "nearest_neighbour_k1": 0.7145, "linear_softmax": 0.528}})
    for module in (old, new):
        module.STATE.mkdir(parents=True, exist_ok=True)
        (module.STATE / "openrsi_targets.json").write_text(targets, encoding="utf-8")
    test = {"poll_seconds": 20}
    life_old = live(old, {**live_cfg, **test}, 6500)
    life_new = live(new, {**kit_cfg, **test}, 6500)
    s = life_new["summary"]
    print("      the scripted life:", json.dumps(s, ensure_ascii=False))
    ok("the life was long enough to mean something",
       s["turns"] >= 120 and s["scored"] >= 4 and {"above/done", "drive/done", "ruu/done", "scan/done"} <= set(s["orders"])
       and "scan/superseded" in s["orders"]
       and {"creation", "build_log", "notice", "transmission", "seventh", "opening", "work", "respond", "study", "plan"} <= set(s["forms"])
       and {"react", "reply", "tag", "say", "ignore"} <= set(s["response modes"])
       and "⊘ metabolism" in life_new["journal"] and "⊘ error" in life_new["journal"], s)
    same(f"all {len(life_new['turns'])} model turns got the same instructions and the same prompt", life_old["turns"], life_new["turns"])
    same(f"all {len(life_new['calls'])} Discord calls are the same", life_old["calls"], life_new["calls"])
    same(f"all {len(life_new['jobs'])} sandbox jobs are the same", life_old["jobs"], life_new["jobs"])
    same("the journal is the same, line for line", life_old["journal"].split("\n"), life_new["journal"].split("\n"))
    for table, rows in life_new["db"].items():
        same(f"table {table} holds the same {len(rows)} rows", life_old["db"][table], rows)
    same("the database has the same columns", life_old["columns"], life_new["columns"])

    # ── another agent from the same machine ─────────────────────────────────
    vex = TMP / "vex-seed"
    seeds.new(vex, {"ID": "vex", "NAME": "Vex", "OWNER": "Sam", "WORLD": "the Foundry", "PLACE": "the Foundry server",
                    "CHANNEL": "#vex-building"})
    errors, _ = seeds.check(vex)
    ok("a fresh seed is refused until its questions are answered", sum("unanswered" in e for e in errors) >= 5, errors)
    for path in vex.rglob("*"):
        if path.is_file() and path.suffix in (".md", ".json"):
            path.write_text(path.read_text(encoding="utf-8").replace("FILL: ", ""), encoding="utf-8")
    cfg = json.loads((vex / "config.example.json").read_text(encoding="utf-8"))
    cfg.update(guild_id="800000000000000001", lab_channel_id="800000000000000002", bot_id="800000000000000003",
               owner_id="800000000000000004", enabled=True)
    (vex / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    errors, warnings = seeds.check(vex, vex / "config.json", [RUU / "config.json"])
    ok("once written, the new seed passes the check beside Ruu's budget", not errors, errors)
    ok("the check says plainly that it has no bench", any("cannot prove improvement" in w for w in warnings), warnings)
    import build_library
    with contextlib.redirect_stdout(io.StringIO()):
        build_library.build(vex, workspace_data=TMP / "nowhere" / "data")
    other = load("vex_machine", MACHINE / "soft_machine.py", TMP / "home-vex", vex)
    other.STATE.parent.mkdir(parents=True, exist_ok=True)  # on a host this is the Hermes home, and exists
    life = live(other, {**cfg, **test, "weekly_ceiling_percent": 60}, 4000, called="vex", name="Vex")
    print("      the other agent's life:", json.dumps(life["summary"], ensure_ascii=False))
    told = json.dumps([life["turns"], life["calls"], life["journal"]], ensure_ascii=False) + other.instructions(False)
    residue = sorted(set(re.findall(r"ruu|scru|syn|openrsi|singularity|scrucraft|seventh output|green interval", told, re.I)))
    ok("nothing the other agent was told or said carries Ruu", not residue, residue)
    ok("it is told who it is", "You write as Vex in Vex's own Discord channel, #vex-building, in the Foundry server." in other.instructions(True)
       and "1.0 is Sam, who made Vex." in other.instructions(True) and "ORIGINAL VEX CHARACTER SPECIFICATION" in other.instructions(False))
    s = life["summary"]
    ok("it lives a full life of its own", s["turns"] >= 80 and {"above/done", "drive/done", "vex/done"} <= set(s["orders"])
       and {"creation", "build_log", "notice", "line", "census", "opening", "work", "respond", "plan"} <= set(s["forms"])
       and s["sandbox jobs"] >= 5, s)
    ok("its state, sandbox socket and columns are its own", other.STATE.name == "vex-lab" and other.HANDS_SOCKET == "/run/vex-hands.sock"
       and "to_vex" in life["columns"] and "to_ruu" not in life["columns"])
    ok("with no bench nothing is scored and no bench is mentioned", s["scored"] == 0 and other.BENCH is None
       and "bench" not in other.WORK["plan"].lower() and "host_score" not in told)
    units = [p.relative_to(TMP / "vex-units").as_posix() for p in seeds.render(vex, TMP / "vex-units", "/opt/soft-machine", "/srv/vex")]
    ok("its service files are named for it", sorted(units) == ["hands/runner.py", "hands/setup.sh", "hands/vex-hands.socket",
       "hands/vex-hands@.service", "vex-frontier-scan.service", "vex-frontier-scan.timer", "vex-soft-machine.service"], units)
    rendered = "".join((TMP / "vex-units" / u).read_text(encoding="utf-8") for u in units)
    ok("and none of them mention Ruu", not re.search(r"ruu", rendered, re.I) and "User=vex-hands" in rendered
       and "--seed /srv/vex --config" in rendered)

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nALL PASSED: {len(PASSED)} checks in {time.time() - began:.0f} s")


if __name__ == "__main__":
    main()
