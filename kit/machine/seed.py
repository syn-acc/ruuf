#!/usr/bin/env python3
"""A seed: everything that makes one agent itself, kept apart from the machine that runs it.

    seed.py check DIR [--config FILE] [--sharing FILE ...]   is this seed fit to run? exit 1 if not
    seed.py new DIR --id vex --name Vex --owner Sam --world "the Foundry" \\
                    --place "the Foundry server" --channel "#vex-building"
    seed.py render DIR OUT [--machine PATH] [--seed-path PATH]   write this agent's service files (installs nothing)

A seed folder holds:
    seed.json        id, name, forms, dice, palette, drives, bench
    persona.md       the character specification
    convictions.md   what it believes: background, never recited
    register.md      how it talks in its own channel: the hard rules
    library.json     what it may read, and which of that it may repeat
    library_src/     papers written for it
    scan.json        optional: its regular look outside

The machine reads a seed with load(); nothing in a seed is code.
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ID_RE = re.compile(r"[a-z][a-z0-9]{1,15}")
SLOT = re.compile(r"\{([a-z_]+)\}")
ROLES = ("plan", "review", "build", "voice", "respond", "study")
UNFILLED = "FILL:"
# Text that reaches a prompt is text the agent may one day post. None of this belongs in it.
LEAKS = [
    (r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "an IP address"),
    (r"https?://", "a link"),
    (r"[\w.+-]+@[\w-]+\.[a-z]{2,}", "an email address"),
    (r"\b[A-Za-z0-9_\-]{32,}\b", "a long token-like string"),
    (r"(?i)\b(?:password|passwd|api[_-]?key|bearer|secret|token)\s*[:=]", "something that reads like a credential"),
    (r"(?<![\w.:/])/(?:data|srv|opt|etc|root|usr|var|home|run)/\S+", "a server path"),
]


def locate(argv=None):
    """The seed folder: --seed DIR on the command line, or $SOFT_MACHINE_SEED."""
    argv = sys.argv if argv is None else argv
    if "--seed" in argv[:-1]:
        return Path(argv[argv.index("--seed") + 1]).resolve()
    if os.environ.get("SOFT_MACHINE_SEED"):
        return Path(os.environ["SOFT_MACHINE_SEED"]).resolve()
    raise SystemExit("no seed: pass --seed DIR or set SOFT_MACHINE_SEED")


def load(folder):
    folder = Path(folder)
    seed = json.loads((folder / "seed.json").read_text(encoding="utf-8"))
    if not ID_RE.fullmatch(str(seed.get("id") or "")):
        raise ValueError("seed id must be 2 to 16 lowercase letters or digits, starting with a letter")
    seed["register"] = (folder / "register.md").read_text(encoding="utf-8").strip()
    scan = folder / "scan.json"
    seed["scan"] = json.loads(scan.read_text(encoding="utf-8")) if scan.is_file() else None
    return seed


def papers(folder):
    """The character papers, read on every speaking turn so an edit takes effect without a restart."""
    persona = (Path(folder) / "persona.md").read_text(encoding="utf-8")
    convictions = (Path(folder) / "convictions.md").read_text(encoding="utf-8")
    if not persona.strip() or len(persona) > 20000 or len(convictions) > 6000:
        raise ValueError("Missing or oversized persona/convictions")
    return persona, convictions


def bench(seed, machine=HERE):
    """The module that scores this agent's work on the host side, or None."""
    name = seed.get("bench")
    if not name:
        return None
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", name):
        raise ValueError("bad bench name")
    spec = importlib.util.spec_from_file_location(f"bench_{name}", Path(machine) / "benches" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def slots(seed, bench_module=None):
    """What the machine's own texts (protocol.py) need to know about this agent."""
    lines = seed["lines"]
    return {"id": seed["id"], "name": seed["name"], "channel": seed["channel"], "place": seed["place"],
            "world": seed["world"], "owner": seed["owner"],
            "rolled": ", ".join(name for name, form in seed["forms"].items() if form["weight"] > 0),
            "numbers_licence": lines["numbers_licence"], "may_say": lines["may_say"],
            "hands_data": lines["hands_data"],
            "bench_reads": getattr(bench_module, "PLAN_READS", ""),
            "bench_plan": getattr(bench_module, "PLAN_RULE", ""),
            "bench_synthesis": getattr(bench_module, "SYNTHESIS_RULE", "")}


def fill(text, values):
    return SLOT.sub(lambda match: values[match.group(1)], text)


# ── check ───────────────────────────────────────────────────────────────────
def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def leaks(text):
    return sorted({what for pattern, what in LEAKS if re.search(pattern, text)})


def check(folder, config=None, sharing=()):
    """Every reason this seed should not run yet. Returns (errors, warnings)."""
    folder, errors, warnings = Path(folder), [], []
    try:
        seed = load(folder)
    except (OSError, ValueError) as exc:
        return [f"cannot load the seed: {exc}"], []
    for key in ("name", "channel", "place", "world", "owner"):
        if not str(seed.get(key) or "").strip():
            errors.append(f"seed.json: `{key}` is missing")
    if not seed.get("called") or not all(isinstance(word, str) and word.strip() for word in seed["called"]):
        errors.append("seed.json: `called` needs at least one word that counts as speaking to it")
    for key in ("numbers_licence", "may_say", "hands_data"):
        if not str((seed.get("lines") or {}).get(key) or "").strip():
            errors.append(f"seed.json: lines.{key} is missing")

    # The papers.
    prompt_text = {"seed.json": "\n".join(strings({k: v for k, v in seed.items() if k not in ("register", "scan")})),
                   "register.md": seed["register"]}
    try:
        prompt_text["persona.md"], prompt_text["convictions.md"] = papers(folder)
    except (OSError, ValueError) as exc:
        errors.append(f"persona.md / convictions.md: {exc} (persona at most 20,000 characters, convictions 6,000)")
    if seed["scan"]:
        prompt_text["scan.json"] = "\n".join(str(seed["scan"].get(key) or "") for key in ("brief", "study", "post"))
    if len(seed["register"]) < 200:
        errors.append("register.md: too short to be a register (the hard rules of its voice go here)")
    for name, text in prompt_text.items():
        if UNFILLED in text:
            errors.append(f"{name}: still has {text.count(UNFILLED)} unanswered `{UNFILLED}` prompt(s)")
        for what in leaks(text):
            errors.append(f"{name}: contains {what}; everything in these files reaches the prompt, and what "
                          "reaches the prompt can reach the channel")

    # Forms: what it makes when nobody asked.
    forms = seed.get("forms") or {}
    rolled = [name for name, form in forms.items() if form.get("weight", 0) > 0]
    for name, form in forms.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,23}", name) or name in ("work", "respond", "study", "plan"):
            errors.append(f"forms: `{name}` is not a usable form name")
        if not isinstance(form.get("weight"), (int, float)) or not str(form.get("brief") or "").strip():
            errors.append(f"forms.{name}: needs a numeric weight and a brief")
    if "opening" not in forms:
        errors.append("forms: `opening` is required (its first words in the channel)")
    if len(rolled) < 2:
        errors.append("forms: at least two forms need a weight above 0, or every unprompted post is the same kind")
    census = seed.get("census")
    if census and (census.get("form") not in forms or not isinstance(census.get("every"), int) or census["every"] < 2):
        errors.append("census: `form` must name a form and `every` must be a whole number above 1")

    # Dice: the visible difference between one post and the next.
    dice = seed.get("dice") or {}
    for die in ("register", "temper", "volume"):
        faces = dice.get(die) or []
        if len(faces) < 2 or not all(f.get("name") and f.get("weight", 0) > 0 and f.get("text") for f in faces):
            errors.append(f"dice.{die}: needs at least two faces, each with a name, a weight above 0 and a text")
    names = {die: [f.get("name") for f in dice.get(die) or []] for die in ("register", "volume")}
    if sum(bool(f.get("announcing")) for f in dice.get("volume") or []) != 1:
        errors.append("dice.volume: exactly one face must be marked `announcing` (used for first words and new orders)")
    if not any(f.get("working") for f in dice.get("volume") or []):
        errors.append("dice.volume: at least one face must be marked `working` (used for the line posted with a run)")
    for face in dice.get("register") or []:
        if face.get("when_announcing") and face["when_announcing"] not in names["register"]:
            errors.append(f"dice.register.{face.get('name')}: when_announcing names a register that does not exist")
    minutes = dice.get("temper_minutes")
    if not (isinstance(minutes, list) and len(minutes) == 2 and 0 < minutes[0] <= minutes[1]):
        errors.append("dice.temper_minutes: needs [shortest, longest] minutes a temper holds")
    if not 3 <= len(seed.get("palette") or []) <= 40:
        errors.append("palette: 3 to 40 emoji it may react with")

    # Drives: the standing motives. Without one the agent has nothing to build when no order is open.
    drives = seed.get("drives") or []
    if not drives:
        warnings.append("drives: none. With no order open it will only post forms; it will never build unprompted")
    for n, drive in enumerate(drives, 1):
        if not str(drive.get("title") or "").strip() or not str(drive.get("brief") or "").strip():
            errors.append(f"drives[{n}]: needs a title and a brief")
        elif len(drive["brief"]) > 600 or len(drive["title"]) > 200:
            errors.append(f"drives[{n}]: the machine keeps 200 characters of a title and 600 of a brief; this is longer")

    # The bench: a number the agent can earn and cannot write.
    try:
        module = bench(seed)
    except (OSError, ValueError, SyntaxError) as exc:
        module = None
        errors.append(f"bench: cannot load `{seed.get('bench')}` ({type(exc).__name__})")
    if seed.get("bench") and module:
        missing = [name for name in ("KEY", "NOTE", "SUBMISSION", "score", "record", "report") if not hasattr(module, name)]
        if missing:
            errors.append(f"bench `{seed['bench']}`: missing {', '.join(missing)}")
        elif drives and not any(module.KEY in drive.get("brief", "") for drive in drives):
            warnings.append(f"bench: no drive mentions `{module.KEY}`, so nothing sends the agent to it")
    if not seed.get("bench"):
        warnings.append("bench: none. The host will post what its runs really printed, but no score is computed "
                        "out of its reach, so it can show work and cannot prove improvement")

    # The library: it only gets to know what it may say.
    try:
        library = json.loads((folder / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        library = None
        errors.append(f"library.json: {exc}")
    if library is not None:
        authored, sources, public = library.get("authored") or {}, library.get("sources") or {}, set(library.get("public") or [])
        for key in list(authored) + list(sources):
            if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,40}", key):
                errors.append(f"library: `{key}` is not a usable key")
        for key in authored:
            path = folder / "library_src" / f"{key}.md"
            if not path.is_file():
                errors.append(f"library: library_src/{key}.md is missing")
                continue
            text = path.read_text(encoding="utf-8")
            if UNFILLED in text:
                errors.append(f"library_src/{key}.md: still has unanswered `{UNFILLED}` prompts")
            if len(text) > 16000:
                warnings.append(f"library_src/{key}.md: {len(text)} characters; only the first 16,000 are kept")
            found = leaks(text)
            if found:
                warnings.append(f"library_src/{key}.md: {', '.join(found)} will be withheld by the redaction")
        for key in public - set(authored) - set(sources):
            errors.append(f"library: `{key}` is marked public and is not in the library")
        internal = sorted((set(authored) | set(sources)) - public)
        if internal:
            warnings.append("library: internal (it may be shaped by these and must never recite them): "
                            + ", ".join(internal) + ". A prompt rule does not hold; leave out anything it must not say")
        if not authored and not sources:
            warnings.append("library: empty. It has nothing to study, so reading orders cannot be given")

    # The look outside.
    scan = seed["scan"]
    if scan:
        for key in ("brief", "study", "post", "sources"):
            if not scan.get(key):
                errors.append(f"scan.json: `{key}` is missing")
        for pattern in [scan.get("about") or "."] + [row[1] for row in scan.get("topics") or []]:
            try:
                re.compile(pattern)
            except re.error:
                errors.append(f"scan.json: not a regular expression: {pattern[:40]}")
        unknown = set(scan.get("sources") or []) - {"arxiv", "hf_papers", "hf_models", "tools"}
        if unknown:
            errors.append(f"scan.json: unknown sources {sorted(unknown)}")

    # Where it lives and what it may spend.
    if config:
        try:
            cfg = json.loads(Path(config).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return errors + [f"config: {exc}"], warnings
        for key in ("guild_id", "lab_channel_id", "bot_id", "owner_id", "poll_seconds", "debounce_seconds",
                    "daily_target", "daily_max", "hourly_max", "turns_hourly_max", "weekly_ceiling_percent", "models"):
            if key not in cfg:
                errors.append(f"config: `{key}` is missing")
        for key in ("guild_id", "lab_channel_id", "bot_id", "owner_id"):
            if key in cfg and not re.fullmatch(r"\d{5,25}", str(cfg[key])):
                errors.append(f"config: `{key}` is not a Discord id")
        if any(form.get("overhears") for form in forms.values()) and not cfg.get("etc_channel_id"):
            errors.append("config: a form overhears another channel, so `etc_channel_id` is needed")
        for role in ROLES:
            if not (cfg.get("routes") or {}).get(role):
                warnings.append(f"config: no route for `{role}` turns; they fall back to the `models` table")
        if "drives" in cfg:
            warnings.append("config: `drives` here is ignored; drives live in seed.json")
        total, others = cfg.get("weekly_ceiling_percent", 0), []
        for other in sharing:
            share = json.loads(Path(other).read_text(encoding="utf-8")).get("weekly_ceiling_percent", 0)
            others.append(share)
            total += share
        if sharing and total > 100:
            errors.append(f"budget: the ceilings on this login add up to {total}% of the week "
                          f"({cfg.get('weekly_ceiling_percent', 0)}% here, {others} elsewhere)")
        elif sharing and total > 80:
            warnings.append(f"budget: the ceilings on this login add up to {total}% of the week, which leaves "
                            f"{100 - total}% for everything else that uses it")
    return errors, warnings


# ── new, render ─────────────────────────────────────────────────────────────
def new(dest, values):
    dest, template = Path(dest), HERE.parent / "seeds" / "_template"
    if dest.exists():
        raise SystemExit(f"{dest} already exists")
    if not ID_RE.fullmatch(values["ID"]):
        raise SystemExit("--id must be 2 to 16 lowercase letters or digits, starting with a letter")
    shutil.copytree(template, dest)
    for path in dest.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".md"):
            text = path.read_text(encoding="utf-8")
            for key, value in values.items():
                text = text.replace(f"@{key}@", json.dumps(value, ensure_ascii=False)[1:-1] if path.suffix == ".json" else value)
            path.write_text(text, encoding="utf-8", newline="\n")
    return dest


def render(folder, out, machine, seed_path):
    """Write the service files one agent needs. Nothing is installed: read them, then install as root."""
    seed, out = load(folder), Path(out)
    module = bench(seed)
    values = {"ID": seed["id"], "NAME": seed["name"], "CHANNEL": seed["channel"], "MACHINE": machine,
              "SEED": seed_path or f"/data/syn.gl/hermes/plugins/{seed['id']}-lab",
              "SUBMISSION": getattr(module, "SUBMISSION", "bench/submission.json"),
              "BENCH": getattr(module, "LABEL", "Bench")}
    written = []
    for template in sorted((HERE / "units").glob("*.in")):
        text = template.read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace(f"@{key}@", value)
        target = out / template.name[:-3].replace("ID-", seed["id"] + "-").replace("__", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        written.append(target)
    return written


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("folder", type=Path)
    c.add_argument("--config", type=Path, help="the instance config: channel, caps, models, budget")
    c.add_argument("--sharing", type=Path, nargs="*", default=[], help="configs of other agents on the same login")
    n = sub.add_parser("new")
    n.add_argument("folder", type=Path)
    for flag in ("id", "name", "owner", "world", "place", "channel"):
        n.add_argument(f"--{flag}", required=True)
    r = sub.add_parser("render")
    r.add_argument("folder", type=Path)
    r.add_argument("out", type=Path)
    r.add_argument("--machine", default="/data/syn.gl/hermes/plugins/soft-machine", help="where the machine is installed")
    r.add_argument("--seed-path", help="where the seed is installed on the host")
    args = p.parse_args()
    if args.command == "check":
        errors, warnings = check(args.folder, args.config, args.sharing)
        for line in warnings:
            print("note   ", line)
        for line in errors:
            print("REFUSED", line)
        print(f"{args.folder.name}: {len(errors)} to fix, {len(warnings)} to know" if errors or warnings
              else f"{args.folder.name}: fit to run")
        return 1 if errors else 0
    if args.command == "new":
        dest = new(args.folder, {key.upper(): getattr(args, key) for key in ("id", "name", "owner", "world", "place", "channel")})
        print(f"seed written to {dest}; answer every `{UNFILLED}` prompt, then run: seed.py check {dest}")
        return 0
    for path in render(args.folder, args.out, args.machine, args.seed_path):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
