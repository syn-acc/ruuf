"""Read an instance's private operator origin and collect its required answers at a terminal.

    python launcher/ruufd.py origin         see which fields have answers
    python launcher/ruufd.py origin ask     answer or update the fields
    python launcher/ruufd.py origin blank   print the unanswered form
"""
import getpass
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
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
    required: bool = True


QUESTIONS = (
    Question("operator.name", "What name should this instance record for its operator?", ""),
    Question("spark.words", "What is the intended purpose of this instance?", ""),
)
FIELD = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def configured(spec):
    if spec is None:
        return QUESTIONS
    if not isinstance(spec, dict) or not isinstance(spec.get("questions"), list):
        raise ValueError("origin.questions must be an array of tables")
    questions = []
    seen = set()
    for entry in spec["questions"]:
        if not isinstance(entry, dict) or set(entry) - {"key", "ask", "why", "required"}:
            raise ValueError("origin.questions entries accept key, ask, why and required")
        key, ask, why, required = (entry.get("key"), entry.get("ask"), entry.get("why", ""), entry.get("required", True))
        if not isinstance(key, str) or not FIELD.fullmatch(key) or key.startswith("given.") or key in seen:
            raise ValueError("origin.questions keys must be unique section.field names outside [given]")
        if not isinstance(ask, str) or not ask.strip() or not isinstance(why, str) or type(required) is not bool:
            raise ValueError("origin.questions needs a question, optional explanatory text and a boolean required flag")
        seen.add(key)
        questions.append(Question(key, ask, why, required))
    if not any(q.required for q in questions):
        raise ValueError("origin.questions needs at least one required answer")
    return tuple(questions)


@dataclass
class Origin:
    path: Path
    questions: tuple
    answers: dict = field(default_factory=dict)   # key -> the operator's words
    given: dict = field(default_factory=dict)     # who answered, and when
    problem: str = ""                             # why the file could not be used, if it could not

    @property
    def open(self):
        """The questions still demanded."""
        return [q.key for q in self.questions if q.required and q.key not in self.answers]

    @property
    def complete(self):
        return not self.problem and not self.open


def path(machine):
    place = machine.places.get("origin")
    return place.path if place else machine.manifest.parent / "origin.toml"


def read(machine):
    where = path(machine)
    questions = machine.origin_questions
    try:
        with open(where, "rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        return Origin(where, questions)
    except PermissionError:
        return Origin(where, questions, problem="this user may not read it")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return Origin(where, questions, problem=f"it cannot be read: {exc}")
    # Historical/private extension fields survive questionnaire changes and edits.
    answers = {f"{section}.{key}": value for section, fields in raw.items()
               if section != "given" and isinstance(fields, dict) for key, value in fields.items()
               if FIELD.fullmatch(f"{section}.{key}") and isinstance(value, str) and value.strip()}
    given = raw.get("given") if isinstance(raw.get("given"), dict) else {}
    return Origin(where, questions, answers, dict(given))


def render(questions, answers, given=None):
    """Serialize the form and any answers retained from an earlier form."""
    quote = lambda value: json.dumps(str(value), ensure_ascii=False).replace("\x7f", "\\u007f")
    lines = ["# Private operator origin. Use `ruufd origin ask` to update the answers."]
    by_section = {}
    configured_keys = {q.key: q for q in questions}
    for key in dict.fromkeys((*configured_keys, *answers)):
        section, name = key.split(".")
        entries = by_section.setdefault(section, [])
        if key in configured_keys:
            question = configured_keys[key]
            hint = question.ask + ("" if question.required else " (optional)")
            entries.extend("# " + line for line in hint.splitlines())
        entries.append(f"{name} = {quote(answers.get(key, ''))}")
    for section, entries in by_section.items():
        lines += ["", f"[{section}]", *entries]
    if given:
        lines += ["", "[given]", *(f"{quote(key)} = {quote(value)}" for key, value in given.items())]
    return "\n".join(lines) + "\n"


def write(machine, answers, by):
    previous = read(machine)
    if previous.problem:
        raise OSError(previous.problem)
    where = previous.path
    where.parent.mkdir(parents=True, exist_ok=True)
    given = {**previous.given, "by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with tempfile.NamedTemporaryFile("w", dir=where.parent, encoding="utf-8", newline="\n", delete=False) as out:
        scratch = Path(out.name)  # a new file, mode 0600
        try:
            out.write(render(machine.origin_questions, {**previous.answers, **answers}, given))
            out.close()
            os.replace(scratch, where)
        finally:
            scratch.unlink(missing_ok=True)
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
        # --local: only this clone's own identity. The account's global one may be a private name.
        done = subprocess.run(["git", "-C", str(machine.manifest.parent), "config", "--local", "user.name"],
                              capture_output=True, text=True, timeout=5)
        facts["git_name"] = done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        pass
    return facts


def context(machine, facts=None, env=os.environ):
    """Offer a name for confirmation on a recognized operator account."""
    facts = facts if facts is not None else who_is_asking(machine, env)
    person = facts["behind"] or facts["login"] or "somebody"
    seen = [f"you are {person} on {facts['host']}" + (", acting as root" if facts["behind"] else "")]
    guesses = {}
    if not any(q.key == "operator.name" for q in machine.origin_questions):
        return seen, guesses
    if not facts["behind"] and any(p.user == person for p in machine.parts.values()):
        seen.append("a configured part uses this account")
        return seen, guesses
    if not (facts["root"] or facts["behind"] or facts["owns_manifest"]):
        return seen, guesses
    # The clone's git identity is the name the operator publishes under, so it is tried first.
    name = next((n for n in (facts["git_name"], facts["full"], person) if n and n != "root"), "")
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
    return (f"origin: {len(origin.open)} of {sum(q.required for q in origin.questions)} question(s) open. Nothing is started until the "
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
    width = max(len(q.key) for q in origin.questions)
    for q in origin.questions:
        if q.key in origin.answers:
            state = "answered"
        elif not q.required:
            state = "open (optional)"
        elif q.key in guesses:
            state = f"open; guess: {guesses[q.key]!r}, not confirmed"
        else:
            state = "open"
        rows.append(f"  {q.key.ljust(width)}  {state}")
    say(f"{sum(q.required for q in origin.questions) - len(origin.open)} of {sum(q.required for q in origin.questions)} answered:")
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
    for q in origin.questions:
        say()
        say(q.ask + (" (optional)" if not q.required else ""))
        if q.why:
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
    left = [q.key for q in origin.questions if q.required and q.key not in answers]
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


def blank(machine, say=print):
    say(render(machine.origin_questions, {}).rstrip("\n"))
    return 0
