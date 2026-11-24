"""origin: where an agent comes from.

A persona, a set of convictions and a standing motive are not the start of an agent. They were
written from something: a person, a place, what the two are to each other, and why the person
wanted an agent there at all. That something is the origin. ruuf does not hold one for anybody. The
launcher demands it from the operator, and starts nothing until it has been given.

The launcher works out what it can from the machine: who is asking, what on this machine is theirs,
and so what they are likely to be to the agent. Those are guesses. A guess becomes an answer only
when the operator confirms it at a terminal. Everything else here only the operator has.

    python launcher/ruufd.py origin         what is known, what is guessed, what is still demanded
    python launcher/ruufd.py origin ask     answer the questions, at a terminal
    python launcher/ruufd.py origin blank   the file with nothing answered, to fill in by hand

The answers are the operator's own words and are kept in one file on the host (the place called
`origin`), readable by its owner only. This program never prints them back and never edits them.

Standard library only.
"""
import getpass
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


@dataclass(frozen=True)
class Question:
    key: str               # section.name in the file
    ask: str
    why: str               # what is made from the answer
    optional: bool = False


QUESTIONS = (
    Question("operator.name", "What should the agent call you?",
             "The name it knows its operator by. Use the name you are happy to be known by in public."),
    Question("operator.about", "Who are you? What you make, what you care about, what you will not have.",
             "The agent's character is cut from this: it is somebody's, and takes after them."),
    Question("relation.you_are", "What are you to this agent: the one who made it, the one who runs it, "
             "someone it works for, a peer?",
             "Decides whose word it weighs, and how heavily."),
    Question("relation.your_word", "When you tell it something, what is that to it: an order, advice, one voice "
             "among many? And what is everybody else's word?",
             "Decides what it does when you and the room want different things."),
    Question("world.name", "Where does it live? Name the place and its people.",
             "The world it speaks into and builds for."),
    Question("world.about", "What is that place for, and who is in it?",
             "What it may assume about whoever it is talking to."),
    Question("spark.words", "Why does this agent exist? As near as you can to how you first said it.",
             "Its objective is made from this. Your words, not a tidy version of them."),
    Question("spark.wants", "Left alone, with nobody asking for anything, what should it be doing?",
             "Its standing motive: what it returns to when no order is open."),
    Question("spark.sources", "What did it grow out of: the texts, scenes, people and machines it is downstream of?",
             "Its library and its taste start here.", optional=True),
)
KEYS = tuple(q.key for q in QUESTIONS)
NEEDED = tuple(q.key for q in QUESTIONS if not q.optional)


@dataclass
class Origin:
    path: Path
    answers: dict = field(default_factory=dict)   # key -> the operator's words
    given: dict = field(default_factory=dict)     # who answered, and when
    problem: str = ""                             # why the file could not be used, if it could not

    @property
    def open(self):
        """The questions still demanded."""
        return [key for key in NEEDED if key not in self.answers]

    @property
    def complete(self):
        return not self.problem and not self.open


def path(machine):
    place = machine.places.get("origin")
    return place.path if place else machine.manifest.parent / "origin.toml"


def read(machine):
    where = path(machine)
    try:
        with open(where, "rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        return Origin(where)
    except PermissionError:
        return Origin(where, problem="this user may not read it")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return Origin(where, problem=f"it cannot be read: {exc}")
    answers = {}
    for key in KEYS:
        section, name = key.split(".")
        value = (raw.get(section) or {}).get(name) if isinstance(raw.get(section), dict) else None
        if isinstance(value, str) and value.strip():
            answers[key] = value.strip()
    given = raw.get("given") if isinstance(raw.get("given"), dict) else {}
    return Origin(where, answers, dict(given))


def render(answers, given=None):
    """The file. Each question sits above its answer, so the file can also be filled in by hand."""
    quote = lambda text: json.dumps(str(text).replace("\x7f", ""), ensure_ascii=False)
    lines = ["# The origin: where this agent comes from, in its operator's own words.",
             "# The launcher demands this and starts nothing without it. Nothing but the operator edits it.",
             "# Answer with `ruufd origin ask`, or fill in the empty strings here."]
    section = None
    for q in QUESTIONS:
        head, name = q.key.split(".")
        if head != section:
            lines += ["", f"[{head}]"]
            section = head
        lines += [f"# {q.ask}" + (" (optional)" if q.optional else ""), f"{name} = {quote(answers.get(q.key, ''))}"]
    if given:
        lines += ["", "[given]", *(f"{key} = {quote(value)}" for key, value in given.items())]
    return "\n".join(lines) + "\n"


def write(machine, answers, by):
    where = path(machine)
    where.parent.mkdir(parents=True, exist_ok=True)
    given = {"by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    scratch = where.with_name(where.name + ".tmp")
    handle = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the owner's eyes only
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
        out.write(render(answers, given))
    os.replace(scratch, where)
    return where


# ── what the launcher can see for itself ─────────────────────────────────────
def who_is_asking(machine, env=os.environ):
    """Plain facts about the caller, from the machine. No judgement yet."""
    facts = {"login": "", "behind": env.get("SUDO_USER", ""), "root": False, "full": "", "git_name": "",
             "owns_manifest": False, "host": socket.gethostname()}
    try:
        facts["login"] = getpass.getuser()
    except Exception:
        pass
    facts["root"] = getattr(os, "geteuid", lambda: -1)() == 0
    try:
        import pwd
        person = pwd.getpwnam(facts["behind"] or facts["login"])
        facts["full"] = person.pw_gecos.split(",")[0].strip()
        facts["owns_manifest"] = os.stat(machine.manifest).st_uid == person.pw_uid
    except (ImportError, KeyError, OSError):
        pass
    try:
        done = subprocess.run(["git", "-C", str(machine.manifest.parent), "config", "user.name"],
                              capture_output=True, text=True, timeout=5)
        facts["git_name"] = done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        pass
    return facts


def context(machine, facts=None, env=os.environ):
    """Who the caller seems to be and what they seem to be to the agent, with guesses for the
    questions a machine can guess at. Returns (lines to show, {key: guess})."""
    facts = facts if facts is not None else who_is_asking(machine, env)
    person = facts["behind"] or facts["login"] or "somebody"
    runs_as = sorted(p.name for p in machine.parts.values() if p.user == person)
    seen = [f"you are {person} on {facts['host']}" + (", acting as root" if facts["behind"] else "")]
    guesses = {}
    if runs_as and not facts["behind"]:
        seen.append(f"{person} is the user that {', '.join(runs_as)} run(s) as: this looks like the agent's "
                    "own account, not a person. An agent does not answer for its operator")
        return seen, guesses
    if facts["root"] or facts["behind"]:
        seen.append("you can start any part as any user: you look like the one who runs this machine")
        guesses["relation.you_are"] = "the one who runs it"
    elif facts["owns_manifest"]:
        seen.append("the manifest is yours: you look like the one who set this up")
        guesses["relation.you_are"] = "the one who set it up"
    else:
        seen.append("nothing in this manifest is yours: you look like a visitor")
        return seen, guesses
    # The clone's git identity is the name the operator publishes under, so it is tried first.
    name = facts["git_name"] or facts["full"] or (person if person != "root" else "")
    if name:
        source = "this clone's git identity" if facts["git_name"] else "this machine's accounts"
        seen.append(f"{source} gives the name {name!r}")
        guesses["operator.name"] = name
    return seen, guesses


# ── the commands ─────────────────────────────────────────────────────────────
def summary(machine):
    """One line for `check`."""
    origin = read(machine)
    if origin.problem:
        return f"origin: {origin.path}: {origin.problem}"
    if origin.complete:
        by = f", given by {origin.given['by']} at {origin.given.get('at', '?')}" if origin.given.get("by") else ""
        return f"origin: answered{by}"
    return (f"origin: {len(origin.open)} of {len(NEEDED)} question(s) open. Nothing is started until the "
            "operator answers them: `ruufd origin`")


def show(machine, say=print, facts=None):
    origin = read(machine)
    seen, guesses = context(machine, facts)
    exists = origin.answers or origin.given or origin.problem
    say(f"origin: {origin.path}" + ("" if exists else " (not written yet)"))
    if origin.problem:
        say(f"  {origin.problem}")
        return 1
    say("what the launcher can see for itself:")
    for line in seen:
        say("  " + line)
    rows = []
    for q in QUESTIONS:
        if q.key in origin.answers:
            state = "answered"
        elif q.optional:
            state = "open (optional)"
        elif q.key in guesses:
            state = f"open; guess: {guesses[q.key]!r}, not confirmed"
        else:
            state = "open"
        rows.append(f"  {q.key.ljust(max(map(len, KEYS)))}  {state}")
    say(f"{len(NEEDED) - len(origin.open)} of {len(NEEDED)} answered:")
    for row in rows:
        say(row)
    if origin.complete:
        if origin.given.get("by"):
            say(f"given by {origin.given['by']} at {origin.given.get('at', '?')}")
        return 0
    say("These are the operator's to answer and nobody else's. At a terminal: `ruufd origin ask`.")
    say("Nothing is started until they are answered.")
    return 1


def ask(machine, read_line=input, say=print, facts=None, terminal=True):
    if not terminal:
        say("refused: there is no terminal here, so there is no operator to ask.")
        say("  The origin is the operator's to give. If you are an agent, do not answer for them:")
        say("  ask them to run `ruufd origin ask`, or to fill in the file `ruufd origin blank` prints.")
        return 1
    origin = read(machine)
    if origin.problem:
        say(f"origin: {origin.path}: {origin.problem}")
        return 1
    facts = facts if facts is not None else who_is_asking(machine)
    seen, guesses = context(machine, facts)
    for line in seen:
        say(line)
    say("Answer in your own words. Enter keeps what is there. Nothing you type is shown again.")
    answers = dict(origin.answers)
    for q in QUESTIONS:
        say()
        say(q.ask + (" (optional)" if q.optional else ""))
        say(f"  {q.why}")
        if q.key in answers:
            say("  [answered already: Enter keeps it]")
        elif q.key in guesses:
            say(f"  [guess: {guesses[q.key]!r}: Enter accepts it]")
        try:
            typed = read_line("> ").strip()
        except EOFError:
            break
        if typed:
            answers[q.key] = typed
        elif q.key not in answers and q.key in guesses:
            answers[q.key] = guesses[q.key]
    left = [key for key in NEEDED if key not in answers]
    say()
    if answers == origin.answers:
        say("nothing was answered, so nothing was written")
    else:
        say(f"written: {write(machine, answers, by=facts['behind'] or facts['login'] or 'unknown')}")
    if left:
        say(f"still open: {', '.join(left)}")
        return 1
    say("the origin is complete")
    return 0


def blank(say=print):
    say(render({}).rstrip("\n"))
    return 0
