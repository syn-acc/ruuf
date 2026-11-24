#!/usr/bin/env python3
"""ruufd: the launcher.

ruuf is a series of processes. This is the one that knows what they are and where everything
lives. It reads one manifest (`ruuf.toml`) and builds two things from it:

  the map            names to places. A part asks for "state" or "decisions" and never builds a path.
  the process table  one entry per part: its command, its user, how it starts, what it may touch.

This is the skeleton. It checks the manifest and answers questions about it. It does not start
anything yet: every part is still run by the unit named in its entry.

    python launcher/ruufd.py check            is the manifest sound
    python launcher/ruufd.py check --host     and does this machine match it
    python launcher/ruufd.py where [name]     a place on the map, or all of them
    python launcher/ruufd.py status           the process table
    python launcher/ruufd.py map              the map as the agent may be shown it: no secret places

Standard library only.
"""
import argparse
import json
import os
import re
import shutil
import stat
import sys
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

    if problems:
        raise ManifestError(problems)
    return Machine(path, dict(agent), places, tuple(ladder), providers, parts)


def check_host(machine, user_exists=None):
    """Compare the manifest with this computer. Returns a list of findings; empty means it matches."""
    if user_exists is None:
        import pwd

        def user_exists(name):
            try:
                pwd.getpwnam(name)
                return True
            except KeyError:
                return False
    findings = []
    for place in machine.places.values():
        if place.planned:
            continue
        try:
            mode = os.stat(place.path).st_mode
        except PermissionError:
            continue  # it is there; this user may not look, which is the point of some places
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
        if part.run and not (shutil.which(part.run[0]) or os.path.exists(part.run[0])):
            findings.append(f"part {part.name}: cannot find {part.run[0]}{note}")
    return findings


def describe_start(part):
    if part.start == "timer":
        return f"every {part.every_seconds}s" if part.every_seconds else f"daily {part.daily_at} UTC"
    if part.start == "socket":
        return f"per job on {part.socket}"
    return part.start


def table(rows, heads):
    widths = [max(len(str(r[i])) for r in [heads, *rows]) for i in range(len(heads))]
    line = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([line(heads), line(["-" * w for w in widths]), *map(line, rows)])


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(prog="ruufd", description="The ruuf launcher (skeleton).")
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
        findings = check_host(machine)
        if not findings:
            say("this machine matches it")
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
        if args.json:
            say(json.dumps([{"part": p.name, "start": describe_start(p), "user": p.user, "sandbox": p.sandbox,
                             "run": list(p.run), "run_by": p.unit or None, "state": "not started by ruufd"}
                            for p in machine.parts.values()], indent=2))
            return 0
        rows = [(p.name, describe_start(p), p.user + (" (sandbox)" if p.sandbox else ""), p.unit or "-")
                for p in machine.parts.values()]
        say(table(rows, ("part", "starts", "user", "run today by")))
        say()
        say("ruufd does not start processes yet. Each part is still run by the unit shown.")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
