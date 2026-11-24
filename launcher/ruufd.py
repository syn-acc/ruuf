#!/usr/bin/env python3
"""ruufd: the launcher.

ruuf is a series of processes. This is the one that knows what they are and where everything
lives. It reads one manifest (`ruuf.toml`) and builds two things from it:

  the map            names to places. A part asks for "state" or "decisions" and never builds a path.
  the process table  one entry per part: its command, its user, how it starts, what it may touch.

It checks the manifest, answers questions about it, and can start and watch parts: always-on parts
and timers, with restarts, time limits and one log per part. It will not start a part that still
names a `unit`: that part belongs to systemd until its entry says otherwise. Socket parts (the
sandboxes) are not started yet.

    python launcher/ruufd.py check            is the manifest sound
    python launcher/ruufd.py check --host     and does this machine match it
    python launcher/ruufd.py where [name]     a place on the map, or all of them
    python launcher/ruufd.py status           the process table, live if the launcher is running
    python launcher/ruufd.py map              the map as the agent may be shown it: no secret places
    python launcher/ruufd.py run --all        start every part the launcher owns, and watch them
    python launcher/ruufd.py run --only a b   or only these
    python launcher/ruufd.py stop             stop the launcher and everything it started

Standard library only.
"""
import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

STARTS = ("always", "timer", "socket", "manual")
KINDS = ("dir", "file", "socket")
RESTARTS = ("no", "on-failure", "always")
NAME = re.compile(r"[a-z][a-z0-9_-]*")
SLOT = re.compile(r"\{([a-z][a-z0-9_-]*)\}")


class ManifestError(ValueError):
    """Everything wrong with a manifest, not only the first thing."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Place:
    name: str
    path: Path
    kind: str
    holds: str
    secret: bool = False   # tokens and logins: never shown to the agent, never reachable from a sandbox
    planned: bool = False  # named now so later steps have somewhere to put things; not expected to exist


@dataclass(frozen=True)
class Part:
    name: str
    run: tuple
    start: str
    user: str
    about: str = ""
    cwd: Path | None = None
    restart: str = "no"
    restart_seconds: int = 0
    every_seconds: int = 0     # timer: this often
    daily_at: str = ""         # timer: or once a day, "HH:MM" UTC
    socket: str = ""           # socket: the place it is started from, one job per connection
    reads: tuple = ()
    writes: tuple = ()
    sandbox: bool = False      # runs code the agent wrote
    optional: bool = False
    limits: dict = field(default_factory=dict)
    unit: str = ""             # what runs it today, until ruufd does


@dataclass(frozen=True)
class Machine:
    manifest: Path
    agent: dict
    places: dict
    ladder: tuple
    providers: dict
    parts: dict
    ship: dict = field(default_factory=dict)  # how a change reaches main: read by routines/ship.py

    def where(self, name):
        try:
            return self.places[name].path
        except KeyError:
            raise KeyError(f"no place called {name!r}; known: {', '.join(sorted(self.places))}") from None

    def safe_map(self):
        """The map as the agent may see it: what exists and what it holds, without secret places."""
        return {p.name: {"holds": p.holds, "kind": p.kind, "planned": p.planned}
                for p in self.places.values() if not p.secret}


def find_manifest(start=None, env=os.environ):
    if env.get("RUUF_MANIFEST"):
        return Path(env["RUUF_MANIFEST"])
    here = Path(start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / "ruuf.toml").is_file():
            return folder / "ruuf.toml"
    return Path(__file__).resolve().parent.parent / "ruuf.toml"


def load(path, env=os.environ):
    """Read a manifest into a Machine, or raise ManifestError listing every problem found."""
    path = Path(path).resolve()
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)
    problems = []
    say = problems.append

    # Vars are the few roots everything else hangs from. RUUF_VAR_<NAME> overrides one, so the
    # same manifest describes the server and a scratch folder.
    values = {"root": str(path.parent)}
    for key, value in (raw.get("vars") or {}).items():
        if not NAME.fullmatch(key) or not isinstance(value, str):
            say(f"vars.{key}: a var is a lowercase name with a string value")
            continue
        values[key] = env.get("RUUF_VAR_" + key.upper().replace("-", "_"), value)

    def expand(text, where_, extra=None):
        def slot(match):
            name = match.group(1)
            if extra and name in extra:
                return str(extra[name])
            if name in values:
                return values[name]
            say(f"{where_}: nothing called {{{name}}}")
            return match.group(0)
        out = str(text)
        for _ in range(4):  # a var may be built from another var
            again = SLOT.sub(slot, out)
            if again == out:
                break
            out = again
        return out

    for key in list(values):
        values[key] = expand(values[key], f"vars.{key}")

    agent = raw.get("agent") or {}
    if not isinstance(agent.get("id"), str) or not NAME.fullmatch(agent.get("id", "")):
        say("agent.id: required, a lowercase name")

    places = {}
    for name, spec in (raw.get("places") or {}).items():
        at = f"places.{name}"
        if not NAME.fullmatch(name):
            say(f"{at}: a place name is lowercase letters, digits, - and _")
        if name in values:
            say(f"{at}: a var has this name already")
        if not isinstance(spec, dict) or not isinstance(spec.get("path"), str):
            say(f"{at}: needs a path")
            continue
        kind = spec.get("kind", "dir")
        if kind not in KINDS:
            say(f"{at}: kind is one of {', '.join(KINDS)}")
        if not spec.get("holds"):
            say(f"{at}: say what it holds; the map is read by people and by the agent")
        target = Path(expand(spec["path"], at))
        if not target.is_absolute():
            target = path.parent / target
        places[name] = Place(name, target, kind, str(spec.get("holds", "")),
                             bool(spec.get("secret")), bool(spec.get("planned")))
    paths = {name: place.path for name, place in places.items()}

    providers = raw.get("providers") or {}
    ladder = []
    rungs = raw.get("ladder")
    if not isinstance(rungs, list) or not rungs:
        say("ladder: required. One or more models, best first; this is the only thing ruuf insists on")
    for i, rung in enumerate(rungs if isinstance(rungs, list) else []):
        if not isinstance(rung, dict) or not rung.get("model") or not rung.get("provider"):
            say(f"ladder[{i}]: needs a provider and a model")
            continue
        if rung["provider"] not in providers:
            say(f"ladder[{i}]: no provider called {rung['provider']!r}")
        ladder.append(dict(rung))
    for name, spec in providers.items():
        for key, value in (spec.items() if isinstance(spec, dict) else ()):
            if isinstance(value, str):
                spec[key] = expand(value, f"providers.{name}.{key}", paths)

    parts = {}
    for name, spec in (raw.get("parts") or {}).items():
        at = f"parts.{name}"
        if not NAME.fullmatch(name):
            say(f"{at}: a part name is lowercase letters, digits, - and _")
        if not isinstance(spec, dict):
            say(f"{at}: must be a table")
            continue
        run = spec.get("run")
        if not isinstance(run, list) or not run or not all(isinstance(a, str) for a in run):
            say(f"{at}.run: the command, as a list of strings")
            run = []
        start = spec.get("start")
        if start not in STARTS:
            say(f"{at}.start: one of {', '.join(STARTS)}")
        if not isinstance(spec.get("user"), str) or not spec.get("user"):
            say(f"{at}.user: every part runs as a named user")
        every, daily = spec.get("every_seconds", 0), spec.get("daily_at", "")
        if start == "timer" and bool(every) == bool(daily):
            say(f"{at}: a timer has every_seconds or daily_at, one of them")
        if start != "timer" and (every or daily):
            say(f"{at}: every_seconds and daily_at belong to timers")
        if daily and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(daily)):
            say(f"{at}.daily_at: HH:MM, in UTC")
        if every and (not isinstance(every, int) or every < 1):
            say(f"{at}.every_seconds: a whole number of seconds")
        socket = spec.get("socket", "")
        if start == "socket":
            if socket not in places or places[socket].kind != "socket":
                say(f"{at}.socket: must name a place of kind socket")
        elif socket:
            say(f"{at}.socket: only parts with start = \"socket\" have one")
        restart = spec.get("restart", "no")
        if restart not in RESTARTS:
            say(f"{at}.restart: one of {', '.join(RESTARTS)}")
        reads, writes = spec.get("reads", []), spec.get("writes", [])
        for label, names in (("reads", reads), ("writes", writes)):
            for place in names if isinstance(names, list) else [None]:
                if place not in places:
                    say(f"{at}.{label}: no place called {place!r}")
        limits = spec.get("limits") or {}
        if not isinstance(limits, dict):
            say(f"{at}.limits: a table, such as {{ memory_mb = 256 }}")
            limits = {}
        if spec.get("sandbox"):
            # A sandbox runs code the agent wrote. These three hold or the manifest is refused.
            if limits.get("network") is not False:
                say(f"{at}: a sandbox must set limits.network = false")
            for place in (*reads, *writes):
                if place in places and places[place].secret:
                    say(f"{at}: a sandbox may not touch the secret place {place!r}")
        cwd = spec.get("cwd")
        parts[name] = Part(
            name=name, run=tuple(expand(a, f"{at}.run", paths) for a in run), start=start or "",
            user=str(spec.get("user", "")), about=str(spec.get("about", "")),
            cwd=Path(expand(cwd, f"{at}.cwd", paths)) if cwd else None,
            restart=restart, restart_seconds=int(spec.get("restart_seconds", 0) or 0),
            every_seconds=every if isinstance(every, int) else 0, daily_at=str(daily),
            socket=socket, reads=tuple(reads) if isinstance(reads, list) else (),
            writes=tuple(writes) if isinstance(writes, list) else (),
            sandbox=bool(spec.get("sandbox")), optional=bool(spec.get("optional")),
            limits=dict(limits), unit=str(spec.get("unit", "")))

    # A sandbox is only separate if it is somebody else. It may not share a user with any part
    # that can read a secret place.
    trusted = {p.user for p in parts.values()
               if any(n in places and places[n].secret for n in (*p.reads, *p.writes))}
    for part in parts.values():
        if part.sandbox and part.user in trusted:
            say(f"parts.{part.name}: runs as {part.user!r}, the same user as a part that reads secrets")

    def expand_all(value, where_):
        if isinstance(value, str):
            return expand(value, where_, paths)
        if isinstance(value, list):
            return [expand_all(v, where_) for v in value]
        return value
    ship = {key: expand_all(value, f"ship.{key}") for key, value in (raw.get("ship") or {}).items()}

    if problems:
        raise ManifestError(problems)
    return Machine(path, dict(agent), places, tuple(ladder), providers, parts, ship)


def check_host(machine, user_exists=None, unseen=None):
    """Compare the manifest with this computer. Returns a list of findings; empty means it matches.
    What this user is not allowed to look at is not a finding: it is added to `unseen` if given."""
    if user_exists is None:
        import pwd

        def user_exists(name):
            try:
                pwd.getpwnam(name)
                return True
            except KeyError:
                return False
    findings, unseen = [], unseen if unseen is not None else []
    for place in machine.places.values():
        if place.planned:
            continue
        try:
            mode = os.stat(place.path).st_mode
        except PermissionError:
            unseen.append(f"place {place.name}: {place.path}")
            continue
        except OSError:
            findings.append(f"place {place.name}: nothing at {place.path}")
            continue
        right = {"dir": stat.S_ISDIR, "file": stat.S_ISREG, "socket": stat.S_ISSOCK}[place.kind](mode)
        if not right:
            findings.append(f"place {place.name}: {place.path} is not a {place.kind}")
    for part in machine.parts.values():
        note = " (optional)" if part.optional else ""
        if not user_exists(part.user):
            findings.append(f"part {part.name}: no user called {part.user}{note}")
        if not part.run or shutil.which(part.run[0]):
            continue
        try:
            os.stat(part.run[0])
        except PermissionError:
            unseen.append(f"part {part.name}: {part.run[0]} (it runs as {part.user})")
        except OSError:
            findings.append(f"part {part.name}: cannot find {part.run[0]}{note}")
    return findings


def describe_start(part):
    if part.start == "timer":
        return f"every {part.every_seconds}s" if part.every_seconds else f"daily {part.daily_at} UTC"
    if part.start == "socket":
        return f"per job on {part.socket}"
    return part.start


# ── running things ───────────────────────────────────────────────────────────
class Refused(RuntimeError):
    """The launcher will not start this, and says why."""


def next_daily(daily_at, now):
    """The next HH:MM UTC after `now` (seconds since the epoch)."""
    hour, minute = map(int, daily_at.split(":"))
    moment = datetime.fromtimestamp(now, timezone.utc)
    due = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= moment:
        due += timedelta(days=1)
    return due.timestamp()


class Job:
    """One row of the process table while the launcher is running."""

    def __init__(self, part):
        self.part = part
        self.proc = None
        self.state = "waiting"   # waiting | running | stopped | failed
        self.started = None
        self.deadline = None     # when limits.runtime_seconds runs out
        self.due = 0.0           # when it next starts
        self.runs = 0
        self.last_exit = None
        self.recent = []         # start times, to notice a part that cannot stay up
        self.log = None

    def row(self):
        return {"part": self.part.name, "state": self.state, "pid": self.proc.pid if self.proc else None,
                "started": self.started, "runs": self.runs, "last_exit": self.last_exit,
                "next_due": self.due if self.state == "waiting" else None}


class Supervisor:
    """Starts the parts it is given, watches them, restarts them by their own rule, and stops them.

    One loop, no threads: `step()` looks at every job once. `run()` calls it until told to stop.
    """

    FLAP = (5, 60.0)  # this many starts inside this many seconds and the part is left down

    def __init__(self, machine, names, as_self=False, clock=time.time):
        self.machine, self.clock, self.as_self = machine, clock, as_self
        self.stopping = False
        self.me = self._whoami()
        for name in ("logs", "launcher"):
            if name not in machine.places or machine.places[name].kind != "dir":
                raise Refused(f"the manifest needs a place called {name!r} (a dir) before anything can run")
        self.jobs = [Job(self._allowed(name)) for name in names]
        if not self.jobs:
            raise Refused("nothing to run: no part in this manifest is the launcher's to start")
        self.state_file = machine.where("launcher") / "ruufd.json"

    @staticmethod
    def _whoami():
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name

    def _allowed(self, name):
        part = self.machine.parts.get(name)
        if part is None:
            raise Refused(f"no part called {name!r}")
        if part.unit:
            raise Refused(f"{name}: still run by {part.unit}. Two of it would both act. "
                          "Stop that unit and remove `unit` from its entry to hand it to the launcher")
        if part.start == "socket":
            raise Refused(f"{name}: socket parts are sandboxes, and the launcher cannot yet apply their limits")
        if part.start == "manual":
            raise Refused(f"{name}: a manual part is started by hand")
        if part.user != self.me and os.geteuid() != 0 and not self.as_self:
            raise Refused(f"{name}: runs as {part.user}, and only root can start a part as someone else "
                          f"(this is {self.me}; --as-self runs it as {self.me}, for scratch folders only)")
        return part

    # A part learns where its places are from its environment, and only the ones it declared.
    def _env(self, part):
        env = dict(os.environ)
        env.update(RUUF_PART=part.name, RUUF_MANIFEST=str(self.machine.manifest), PYTHONUNBUFFERED="1")
        for name in (*part.reads, *part.writes):
            env["RUUF_PLACE_" + name.upper().replace("-", "_")] = str(self.machine.where(name))
        return env

    def _note(self, job, text):
        stamp = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        job.log.write(f"[ruufd {stamp}] {text}\n".encode())
        job.log.flush()

    def _start(self, job):
        part, now = job.part, self.clock()
        options = {}
        if part.user != self.me and os.geteuid() == 0:
            import pwd
            who = pwd.getpwnam(part.user)
            options = {"user": who.pw_uid, "group": who.pw_gid, "extra_groups": os.getgrouplist(part.user, who.pw_gid)}
            env_home = {"HOME": who.pw_dir, "USER": part.user, "LOGNAME": part.user}
        else:
            env_home = {}
        try:
            job.proc = subprocess.Popen(part.run, cwd=part.cwd, env={**self._env(part), **env_home},
                                        stdin=subprocess.DEVNULL, stdout=job.log, stderr=subprocess.STDOUT,
                                        start_new_session=True, **options)
        except OSError as exc:
            job.state, job.last_exit = "failed", f"could not start: {exc.strerror or exc}"
            self._note(job, job.last_exit)
            return
        job.state, job.started, job.runs = "running", now, job.runs + 1
        limit = part.limits.get("runtime_seconds")
        job.deadline = now + limit if limit else None
        job.recent = [t for t in job.recent if now - t < self.FLAP[1]] + [now]
        self._note(job, f"started pid {job.proc.pid} as {part.user if options else self.me}")

    def _signal(self, job, sig):
        try:
            os.killpg(job.proc.pid, sig)  # its own session: this reaches everything it spawned
        except ProcessLookupError:
            pass

    def _ended(self, job, code):
        part, now = job.part, self.clock()
        job.proc, job.last_exit, job.deadline = None, code, None
        self._note(job, f"exited {code}")
        if self.stopping:
            job.state = "stopped"
        elif part.start == "timer":
            # Counted from when the run ended, as the timers it replaces do.
            job.state = "waiting"
            job.due = now + part.every_seconds if part.every_seconds else next_daily(part.daily_at, now)
        elif part.restart == "always" or (part.restart == "on-failure" and code != 0):
            if len(job.recent) >= self.FLAP[0]:
                job.state = "failed"
                self._note(job, f"{self.FLAP[0]} starts in {int(self.FLAP[1])}s: left down")
            else:
                job.state, job.due = "waiting", now + part.restart_seconds
        else:
            job.state = "stopped" if code == 0 else "failed"

    def step(self):
        now = self.clock()
        for job in self.jobs:
            if job.proc is not None:
                code = job.proc.poll()
                if code is not None:
                    self._ended(job, code)
                elif job.deadline and now >= job.deadline:
                    self._note(job, f"over its {job.part.limits['runtime_seconds']}s limit: killed")
                    self._signal(job, signal.SIGKILL)
                    job.deadline = None
            elif job.state == "waiting" and not self.stopping and now >= job.due:
                self._start(job)
        self._write_state()

    def _write_state(self):
        state = {"pid": os.getpid(), "manifest": str(self.machine.manifest), "at": self.clock(),
                 "parts": [job.row() for job in self.jobs]}
        text = json.dumps(state)
        if text == getattr(self, "_last_state", None):
            return
        scratch = self.state_file.with_suffix(".tmp")
        scratch.write_text(text, encoding="utf-8")
        os.replace(scratch, self.state_file)
        self._last_state = text

    def open(self):
        logs = self.machine.where("logs")
        logs.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        other = read_state(self.machine)
        if other and other["alive"] and other["pid"] != os.getpid():
            raise Refused(f"a launcher is already running for this manifest (pid {other['pid']})")
        now = self.clock()
        for job in self.jobs:
            job.log = open(logs / f"{job.part.name}.log", "ab")
            if job.part.start == "timer" and job.part.daily_at:
                job.due = next_daily(job.part.daily_at, now)

    def close(self, grace=10.0):
        """Ask everything to stop, wait, then insist."""
        self.stopping = True
        for job in self.jobs:
            if job.proc is not None:
                self._signal(job, signal.SIGTERM)
        end = time.monotonic() + grace
        while any(job.proc is not None for job in self.jobs):
            if time.monotonic() > end:
                for job in self.jobs:
                    if job.proc is not None:
                        self._signal(job, signal.SIGKILL)
                end = float("inf")
            self.step()
            time.sleep(0.05)
        for job in self.jobs:
            if job.state == "waiting":
                job.state = "stopped"
            if job.log:
                job.log.close()
        try:
            self.state_file.unlink()
        except OSError:
            pass

    def run(self, tick=0.5):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stopping", True))
        self.open()
        try:
            while not self.stopping:
                self.step()
                time.sleep(tick)
        finally:
            self.close()
        return 0


def read_state(machine):
    """What a running launcher last wrote, with whether it is still alive. None if there is none."""
    place = machine.places.get("launcher")
    if place is None:
        return None
    try:
        state = json.loads((place.path / "ruufd.json").read_text(encoding="utf-8"))
        os.kill(state["pid"], 0)
        state["alive"] = True
    except ProcessLookupError:
        state["alive"] = False
    except PermissionError:
        state["alive"] = True  # it is there; it is someone else's
    except (OSError, ValueError, KeyError):
        return None
    return state


def owned(machine):
    """The parts the launcher may start today: no unit holds them, and they are not sandboxes."""
    return [p.name for p in machine.parts.values() if not p.unit and p.start in ("always", "timer")]


def table(rows, heads):
    widths = [max(len(str(r[i])) for r in [heads, *rows]) for i in range(len(heads))]
    line = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([line(heads), line(["-" * w for w in widths]), *map(line, rows)])


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(prog="ruufd", description="The ruuf launcher.")
    parser.add_argument("--manifest", help="path to ruuf.toml; default: $RUUF_MANIFEST, then the nearest one")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="is the manifest sound")
    c.add_argument("--host", action="store_true", help="also compare it with this machine")
    w = sub.add_parser("where", help="a place on the map")
    w.add_argument("name", nargs="?")
    w.add_argument("--json", action="store_true")
    s = sub.add_parser("status", help="the process table")
    s.add_argument("--json", action="store_true")
    sub.add_parser("map", help="the map as the agent may be shown it")
    r = sub.add_parser("run", help="start parts and watch them, in the foreground")
    which = r.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="every part the launcher owns")
    which.add_argument("--only", nargs="+", metavar="PART")
    r.add_argument("--as-self", action="store_true",
                   help="run parts as the current user whatever their entry says; for scratch folders")
    sub.add_parser("stop", help="stop the running launcher and everything it started")
    args = parser.parse_args(argv)
    say = lambda text="": print(text, file=out)

    try:
        machine = load(args.manifest or find_manifest())
    except FileNotFoundError as exc:
        say(f"no manifest: {exc.filename}")
        return 2
    except ManifestError as exc:
        say(f"{len(exc.problems)} problem(s) in the manifest:")
        for problem in exc.problems:
            say("  " + problem)
        return 1

    if args.command == "check":
        say(f"manifest sound: {machine.manifest}")
        say(f"  agent {machine.agent['id']}, {len(machine.ladder)} rung(s), "
            f"{len(machine.places)} places, {len(machine.parts)} parts")
        if not args.host:
            return 0
        unseen = []
        findings = check_host(machine, unseen=unseen)
        if unseen:
            say(f"{len(unseen)} thing(s) this user may not look at, so they were not checked:")
            for item in unseen:
                say("  " + item)
        if not findings:
            say("this machine matches it" + (" as far as this user can see" if unseen else ""))
            return 0
        say(f"{len(findings)} difference(s) on this machine:")
        for finding in findings:
            say("  " + finding)
        return 1

    if args.command == "where":
        if args.name:
            try:
                say(str(machine.where(args.name)))
            except KeyError as exc:
                say(exc.args[0])
                return 1
            return 0
        if args.json:
            say(json.dumps({p.name: str(p.path) for p in machine.places.values()}, indent=2))
            return 0
        rows = [(p.name, p.kind + (" (planned)" if p.planned else "") + (" (secret)" if p.secret else ""),
                 p.path, p.holds) for p in machine.places.values()]
        say(table(rows, ("place", "kind", "path", "holds")))
        return 0

    if args.command == "map":
        say(json.dumps(machine.safe_map(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "status":
        live = read_state(machine)
        live = live if live and live["alive"] else None
        rows_live = {row["part"]: row for row in live["parts"]} if live else {}

        def state_of(p):
            row = rows_live.get(p.name)
            if row:
                extra = f" pid {row['pid']}" if row["pid"] else ""
                if row["last_exit"] is not None and row["state"] != "running":
                    extra += f" (last exit {row['last_exit']})"
                return f"{row['state']}{extra}, {row['runs']} run(s)"
            return f"systemd: {p.unit}" if p.unit else "not started"
        if args.json:
            say(json.dumps({"launcher": {"running": bool(live), "pid": live["pid"] if live else None},
                            "parts": [{"part": p.name, "start": describe_start(p), "user": p.user,
                                       "sandbox": p.sandbox, "run": list(p.run), "run_by": p.unit or None,
                                       "live": rows_live.get(p.name)} for p in machine.parts.values()]}, indent=2))
            return 0
        rows = [(p.name, describe_start(p), p.user + (" (sandbox)" if p.sandbox else ""), state_of(p))
                for p in machine.parts.values()]
        say(table(rows, ("part", "starts", "user", "state")))
        say()
        say(f"launcher running, pid {live['pid']}" if live else "launcher not running")
        return 0

    if args.command == "run":
        try:
            supervisor = Supervisor(machine, owned(machine) if args.all else args.only, as_self=args.as_self)
            return supervisor.run()
        except Refused as exc:
            say(f"refused: {exc}")
            return 1

    if args.command == "stop":
        live = read_state(machine)
        if not live or not live["alive"]:
            say("launcher not running")
            return 1
        os.kill(live["pid"], signal.SIGTERM)
        for _ in range(150):
            time.sleep(0.1)
            now = read_state(machine)
            if not now or not now["alive"]:
                say(f"stopped launcher pid {live['pid']}")
                return 0
        say(f"launcher pid {live['pid']} was asked to stop and has not yet")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
