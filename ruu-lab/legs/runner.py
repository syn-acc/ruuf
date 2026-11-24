#!/usr/bin/python3 -I
"""ruu legs: compute Ruu can start for itself somewhere other than this machine.

Hands are the local sandbox: Ruu writes code and the host runs it here, with no network.
Legs are the opposite trade: real compute elsewhere (GPUs in the cloud), but Ruu never sends
code to it. Ruu chooses the PARAMETERS of an experiment from a fixed menu; this runner, on the
trusted side, turns them into a job, starts it, meters it and reads the scored result back.
The credential for the compute stays with the kind's own user. Nothing here returns it, and
nothing Ruu writes is ever executed by legs.

One request per connection, one JSON object on stdin, one JSON object on stdout:
  {"action": "status"}                      what legs exist, what they cost, what has been walked
  {"action": "stride", "kind": "colab", "params": {...}}   start one experiment (a stride)
  {"action": "result", "id": "..."}         where a stride has got to, and its scored outcome

A KIND is one sort of legs. `colab` (a rented GPU session driving the child system) is the first.
Adding another means adding an entry to KINDS with the same three functions.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

STATE = "/data/ruu-legs"
RULES = json.load(open("/opt/ruu-legs/rules.json", encoding="utf-8"))
SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"


def load():
    try:
        return json.load(open(STATE + "/strides.json", encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save(strides):
    tmp = STATE + "/strides.json.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(strides[-200:], handle, indent=1)
    os.replace(tmp, STATE + "/strides.json")


def refuse(why):
    return {"ok": False, "refused": why}


def integer(params, key, low, high, default):
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{key} must be a whole number from {low} to {high}")
    return value


# ── kind: colab ─────────────────────────────────────────────────────────────
class Colab:
    """A rented GPU session that runs one training screen of the child system: the base model
    attempts practice items, this machine checks the attempts and keeps some, the model is tuned
    on what was kept, and the tuned child answers the same test items as the baseline. Every
    score is computed here, on items and answers the GPU never sees the key to."""
    rules = RULES["colab"]
    db_path = "/data/ruu-child/child.sqlite3"

    def db(self):
        db = sqlite3.connect("file:" + self.db_path + "?mode=ro", uri=True, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def families(self):
        rows = self.db().execute(
            "select family, round(avg(correct)*100,1) pct, count(distinct item_id) n from results "
            "where run_id=? and grp='seen' group by family order by family", (self.rules["parent"],)).fetchall()
        return {r["family"]: {"baseline_percent": r["pct"], "test_items": r["n"]} for r in rows}

    def spent(self):
        cut = time.time() - 7 * 86400
        rows = self.db().execute("select gpu, started, ended, deadline, units_before, units_after from jobs "
                                 "where started>?", (cut,)).fetchall()
        rate, total = json.load(open("/opt/ruu-child/allow.json"))["nominal_units_per_hour"], 0.0
        for r in rows:
            if r["units_before"] is not None and r["units_after"] is not None:
                total += max(0.0, r["units_before"] - r["units_after"])
            else:  # still up, or the balance could not be read: charge the nominal rate
                total += rate.get(r["gpu"], 6.0) * ((r["ended"] or min(time.time(), r["deadline"])) - r["started"]) / 3600
        return round(total, 2)

    def session_up(self):
        return self.db().execute(
            "select count(*) from jobs where status in ('running','open','stop-failed')").fetchone()[0] > 0

    def describe(self):
        allow = json.load(open("/opt/ruu-child/allow.json"))
        return {
            "what": "one training screen of an open model on a rented GPU; scores computed on this machine",
            "model": self.rules["model"], "baseline_run": self.rules["parent"],
            "units_last_7_days": self.spent(), "units_weekly_ceiling": allow["units_per_week"],
            "units_per_stride_about": self.rules["units_about"], "minutes_per_stride_about": self.rules["minutes_about"],
            "strides_per_day": self.rules["strides_per_day"], "session_up": self.session_up(),
            "families": self.families(), "notes": self.rules["notes"],
            "params": {
                "families": "1 to 12 names from `families`: the same families are practised and tested",
                "pool": "practice items per family, 4 to 60", "pool_offset": "skip this many practice items per family, "
                "0 to 100 (pool_offset + pool at most 110); use it to train on items no earlier stride used",
                "samples": "attempts per practice item, 1 to 4",
                "policy": "list of 1 or 2 from " + json.dumps(self.rules["policies"]) + "; the first is the idea, "
                          "a second is a control cut to the same size",
                "hard_only": "true keeps only items the model gets wrong at least half the time",
                "seed": "0 to 99; a different seed is a repeat of the same idea",
                "rank": "optional, 4 to 64", "lr": "optional, 0.00001 to 0.001", "epochs": "optional, 1 to 4",
                "screen": "test items per family, 0 for all the baseline answered, else 5 to 100",
            },
        }

    def command(self, params):
        known = self.families()
        fams = params.get("families")
        if not isinstance(fams, list) or not 1 <= len(fams) <= 12 or any(f not in known for f in fams) \
                or len(set(fams)) != len(fams):
            raise ValueError("families must be 1 to 12 different names from the list in status")
        pool = integer(params, "pool", 4, 60, 12)
        offset = integer(params, "pool_offset", 0, 100, 0)
        if pool + offset > 110:
            raise ValueError("pool_offset + pool must be at most 110")
        policy = params.get("policy", ["verified-short"])
        if not isinstance(policy, list) or not 1 <= len(policy) <= 2 or any(p not in self.rules["policies"] for p in policy) \
                or len(set(policy)) != len(policy):
            raise ValueError("policy must be a list of 1 or 2 different known policies")
        screen = integer(params, "screen", 0, 100, 0)
        if 0 < screen < 5:
            raise ValueError("screen must be 0 or at least 5")
        clean = {"families": fams, "pool": pool, "pool_offset": offset, "samples": integer(params, "samples", 1, 4, 4),
                 "policy": policy, "hard_only": params.get("hard_only") is True, "seed": integer(params, "seed", 0, 99, 0),
                 "screen": screen}
        argv = ["generation", "--parent", self.rules["parent"], "--family", *fams, "--pool", str(pool),
                "--pool-offset", str(offset), "--samples", str(clean["samples"]), "--policy", *policy,
                "--seed", str(clean["seed"]), "--screen", str(screen), "--gpu", self.rules["gpu"],
                "--hours", str(self.rules["hours"])]
        if clean["hard_only"]:
            argv.append("--hard-only")
        if params.get("rank") is not None:
            clean["rank"] = integer(params, "rank", 4, 64, 16)
            argv += ["--rank", str(clean["rank"])]
        if params.get("epochs") is not None:
            clean["epochs"] = integer(params, "epochs", 1, 4, 2)
            argv += ["--epochs", str(clean["epochs"])]
        if params.get("lr") is not None:
            lr = params["lr"]
            if isinstance(lr, bool) or not isinstance(lr, (int, float)) or not 1e-5 <= lr <= 1e-3:
                raise ValueError("lr must be from 0.00001 to 0.001")
            clean["lr"] = float(lr)
            argv += ["--lr", repr(float(lr))]
        return clean, argv

    def start(self, stride_id, argv):
        allow = json.load(open("/opt/ruu-child/allow.json"))
        worst = allow["nominal_units_per_hour"][self.rules["gpu"]] * self.rules["hours"]
        if self.spent() + worst > allow["units_per_week"]:
            raise RuntimeError(f"the weekly ceiling of {allow['units_per_week']} units would be passed")
        if self.session_up():
            raise RuntimeError("a session is already up")
        log = f"{STATE}/logs/{stride_id}.log"
        unit = f"ruu-legs-stride-{stride_id}"
        subprocess.run([
            SYSTEMD_RUN, "--quiet", "--collect", "--unit", unit, "--uid=ruu-child", "--gid=ruu-child",
            "--working-directory=/data/ruu-child", "--setenv=HOME=/data/ruu-child", "--setenv=PYTHONDONTWRITEBYTECODE=1",
            "--property=NoNewPrivileges=yes", "--property=UMask=0077",
            f"--property=StandardOutput=append:{log}", f"--property=StandardError=append:{log}",
            f"--property=RuntimeMaxSec={int(self.rules['hours'] * 3600 + 900)}",
            "/data/ruu-child/venv/bin/python", "-W", "ignore", "/opt/ruu-child/launch.py", *argv],
            check=True, timeout=30, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        return unit

    LINE = re.compile(r"^\s+(seen|held_out|public|all)\s+([\d.]+) -> ([\d.]+)\s+diff\s+(-?[\d.]+) \[(-?[\d.]+), (-?[\d.]+)\]"
                      r".*?gained (\d+) lost (\d+) p=([\d.e+-]+)\s+n=(\d+)\s+(CLEAR OF ZERO|inside the noise)", re.M)

    def outcome(self, stride):
        """Read what the finished job printed. The numbers were computed by score.py on this machine."""
        try:
            text = open(f"{STATE}/logs/{stride['id']}.log", encoding="utf-8", errors="replace").read()
        except OSError:
            text = ""
        arms = []
        for head in re.finditer(r"^== (\S+) \((\S+), served as (\w+)\) against the parent\n((?:.*\n?){1,8})", text, re.M):
            groups = {}
            for m in self.LINE.finditer(head.group(4)):
                groups[m.group(1)] = {
                    "baseline_percent": float(m.group(2)), "child_percent": float(m.group(3)),
                    "difference_points": float(m.group(4)), "interval": [float(m.group(5)), float(m.group(6))],
                    "gained": int(m.group(7)), "lost": int(m.group(8)), "p": float(m.group(9)), "items": int(m.group(10)),
                    "clear_of_zero": m.group(11) == "CLEAR OF ZERO"}
            arms.append({"policy": head.group(1), "tune": head.group(2), "result": groups})
        job = self.db().execute("select * from jobs where started>=? order by id limit 1", (stride["started"] - 5,)).fetchone()
        out = {"arms": arms}
        if job:
            out["minutes"] = round(((job["ended"] or time.time()) - job["started"]) / 60, 1)
            if job["units_before"] is not None and job["units_after"] is not None:
                out["units_spent"] = round(job["units_before"] - job["units_after"], 2)
        kept = re.findall(r"kept (\d+)", text)
        if kept:
            out["examples_kept"] = [int(k) for k in kept[:4]]
        if not arms:  # say why, without handing back paths, addresses or anything long
            tail = re.sub(r"(/[\w.\-]+){2,}|https?://\S+|\b\d{1,3}(\.\d{1,3}){3}\b", "[…]", text[-1500:])
            out["problem"] = tail.strip().splitlines()[-6:]
        return out


KINDS = {"colab": Colab()}


def refresh(strides):
    """Move finished strides from running to done or failed, reading their outcome once."""
    changed = False
    for stride in strides:
        if stride["status"] != "running":
            continue
        state = subprocess.run([SYSTEMCTL, "is-active", stride["unit"]], capture_output=True, text=True).stdout.strip()
        if state in ("active", "activating"):
            continue
        stride.update(KINDS[stride["kind"]].outcome(stride))
        stride["ended"] = time.time()
        stride["status"] = "done" if stride.get("arms") else "failed"
        changed = True
    if changed:
        save(strides)
    return strides


def public(stride):
    keep = ("id", "kind", "status", "params", "why", "arms", "minutes", "units_spent", "examples_kept", "problem")
    out = {k: stride[k] for k in keep if k in stride}
    out["started_utc"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(stride["started"]))
    return out


def handle(request):
    action = request.get("action")
    strides = refresh(load())
    if action == "status":
        return {"ok": True, "kinds": {name: kind.describe() for name, kind in KINDS.items()},
                "running": [public(s) for s in strides if s["status"] == "running"],
                "recent": [public(s) for s in strides if s["status"] != "running"][-8:]}
    if action == "result":
        hit = [s for s in strides if s["id"] == request.get("id")]
        return {"ok": True, "stride": public(hit[0])} if hit else refuse("no such stride")
    if action == "stride":
        kind = KINDS.get(request.get("kind"))
        if not kind:
            return refuse("unknown kind of legs")
        if any(s["status"] == "running" for s in strides):
            return refuse("a stride is already running; legs take one stride at a time")
        today = [s for s in strides if s["started"] > time.time() - 86400 and s["kind"] == request["kind"]]
        if len(today) >= kind.rules["strides_per_day"]:
            return refuse(f"{kind.rules['strides_per_day']} strides in 24 hours is the limit")
        params = request.get("params")
        if not isinstance(params, dict):
            return refuse("params must be an object")
        try:
            clean, argv = kind.command(params)
        except ValueError as error:
            return refuse(str(error))
        if request.get("dry") is True:  # everything checked, nothing started, nothing spent
            return {"ok": True, "dry": True, "params": clean, "would_run": argv}
        stride = {"id": time.strftime("%m%d-%H%M%S", time.gmtime()), "kind": request["kind"], "status": "running",
                  "params": clean, "why": str(request.get("why") or "")[:300], "started": time.time()}
        try:
            stride["unit"] = kind.start(stride["id"], argv)
        except RuntimeError as error:
            return refuse(str(error))
        except (subprocess.SubprocessError, OSError):
            return refuse("the job could not be started")
        save(strides + [stride])
        return {"ok": True, "stride": public(stride)}
    return refuse("unknown action")


def main():
    raw = sys.stdin.buffer.read(16384)
    try:
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        reply = refuse("one JSON object per connection")
    else:
        try:
            reply = handle(request)
        except Exception as error:  # never a traceback to the caller
            print(f"legs runner error: {error!r}", file=sys.stderr)
            reply = refuse("legs failed inside; see the journal")
    sys.stdout.write(json.dumps(reply))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
