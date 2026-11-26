#!/usr/bin/env python3
"""ship: how a change reaches main.

A rule that used to be remembered, written as a program: a branch is proven on the real machine,
and what is proven lands on main at once. Nothing reaches main any other way.

    python routines/ship.py status           every branch ahead of main: how far, and is it proven
    python routines/ship.py prove [branch]   put the branch tip beside the running system and run the checks
    python routines/ship.py land [branch]    fast-forward main to a proven tip and push it, or refuse and say why

`prove` reads [ship] from --ship-config PATH, the clone's .git/ruuf/ship.toml, or the
candidate's ruuf.toml, in that order. External files contain literal locations, users and check
argument lists. It copies the tip to its own folder on the configured machine, runs each check as
each user and records the tree and proof settings inside this clone's .git folder.

`land` refuses unless all of these hold, and lists every one that does not:
  1. main is an ancestor of the tip: a fast-forward, never a merge, never a force;
  2. there is a receipt for exactly this tree and every check on it passed;
  3. every commit is authored and committed by this clone's own git identity;
  4. no commit message carries a trailer naming anyone or anything else;
  5. no word from this clone's private list appears in the commits or their changes.

The private list is `<.git>/ruuf/private-words`, one per line: names that must never be published.
It lives inside .git, so it cannot be committed. An empty file means there are none.

`status` exits 1 when a proven branch has not landed, or a branch is further ahead of main than
[ship] max_ahead: a tested branch is not left to drift.

Standard library only. Python 3.11 or later.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "launcher"))
import ruufd  # noqa: E402

TRAILER = re.compile(r"(?im)^[ \t]*(co-authored-by|signed-off-by|claude-session|generated-by|on-behalf-of)[ \t]*:")
SHA = re.compile(r"[0-9a-f]{40}")


class Refused(RuntimeError):
    def __init__(self, reasons):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("; ".join(self.reasons))


class Repo:
    def __init__(self, path, ship_config=None):
        self.path = Path(path)
        self.ship_config = ship_config

    def git(self, *args, check=True, data=None, text=True):
        done = subprocess.run(["git", "-C", str(self.path), *args], input=data, capture_output=True, text=text)
        if check and done.returncode:
            raise Refused(f"git {' '.join(args)}: {(done.stderr if text else done.stderr.decode()).strip()}")
        return done.stdout.strip() if text else done.stdout

    def ok(self, *args):
        return subprocess.run(["git", "-C", str(self.path), *args], capture_output=True).returncode == 0

    def private(self):
        return Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir")) / "ruuf"

    def receipt_path(self, tree):
        return self.private() / "receipts" / f"{tree}.json"

    def receipt(self, tree):
        try:
            return json.loads(self.receipt_path(tree).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def tip(self, branch):
        return self.git("rev-parse", "--verify", f"{branch}^{{commit}}")

    def tree(self, commit):
        return self.git("rev-parse", f"{commit}^{{tree}}")

    def ahead(self, base, tip):
        return int(self.git("rev-list", "--count", f"{base}..{tip}"))

    def current(self):
        return self.git("rev-parse", "--abbrev-ref", "HEAD")


def external_config(repo):
    private = repo.private()
    path = repo.ship_config if repo.ship_config is not None else private / "ship.toml"
    if repo.ship_config is None and not os.path.lexists(path):
        return None
    try:
        if path.is_symlink():
            raise Refused("operator ship config must be a regular file")
        path = path.resolve(strict=True)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            raise Refused("operator ship config must be owned by the operator or root and protected from writes by others")
        for parent in path.parents:
            info = parent.stat()
            if info.st_uid not in (0, os.geteuid()) or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
                raise Refused(f"untrusted ship config directory: {parent}")
    except OSError as exc:
        raise Refused(f"cannot read operator ship config: {exc}") from None
    if not path.is_relative_to(private):
        worktrees = repo.git("worktree", "list", "--porcelain", "-z", text=False).split(b"\0")
        if any(path.is_relative_to(Path(os.fsdecode(row[9:])).resolve()) for row in worktrees if row.startswith(b"worktree ")):
            raise Refused("operator ship config must be outside candidate worktrees")
    return path


def settings(repo, commit):
    external = external_config(repo)
    label = str(external) if external else f"ruuf.toml in {commit[:7]}"
    try:
        if external:
            with external.open("rb") as source:
                ship = ruufd.tomllib.load(source).get("ship", {})
        else:
            raw = repo.git("show", f"{commit}:ruuf.toml", text=False)
            # The legacy manifest may expand {vars} and places through the launcher.
            repo.private().mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=repo.private(), suffix=".toml") as source:
                source.write(raw)
                source.flush()
                ship = ruufd.load(Path(source.name), env={}).ship
    except (OSError, ValueError) as exc:
        raise Refused(f"{label}: {exc}") from None
    if not isinstance(ship, dict):
        raise Refused(f"{label}: expected a [ship] table")
    def argv(value):
        return isinstance(value, list) and value and value[0] and all(isinstance(item, str) for item in value)
    if not argv(ship.get("remote")) or not argv(ship.get("users")) or not all(ship["users"]):
        raise Refused(f"{label}: [ship].remote and users must be nonempty string lists")
    checks = ship.get("checks")
    if not isinstance(checks, list) or not checks or not all(argv(command) for command in checks):
        raise Refused(f"{label}: [ship].checks requires nonempty command argument lists")
    proofs = ship.get("proofs")
    if not isinstance(proofs, str) or not proofs.startswith("/") or ".." in proofs:
        raise Refused(f"{label}: [ship].proofs must be an absolute path")
    for key in ("keep", "max_ahead"):
        if key in ship and (type(ship[key]) is not int or ship[key] < 0):
            raise Refused(f"{label}: [ship].{key} must be a nonnegative integer")
    return ship


def proof_policy(ship):
    keys = ("remote", "proofs", "owner", "users", "checks")
    data = json.dumps({k: ship.get(k) for k in keys}, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def matches_policy(repo, tip, receipt):
    if "proof_policy" not in receipt:
        # Existing receipts were bound to the manifest inside the proven tree.
        return external_config(repo) is None
    return proof_policy(settings(repo, tip)) == receipt["proof_policy"]


def quote(text):
    return "'" + str(text).replace("'", "'\\''") + "'"


def prove(repo, branch, say=print):
    tip = repo.tip(branch)
    tree = repo.tree(tip)
    ship = settings(repo, tip)
    remote = [str(a) for a in ship["remote"]]

    def there(script, data=None):
        return subprocess.run([*remote, script], input=data, capture_output=True)

    who = there("id -un")
    if who.returncode:
        raise Refused(f"cannot reach the machine: {who.stderr.decode(errors='replace').strip()}")
    login = who.stdout.decode().strip()
    folder = f"{str(ship['proofs']).rstrip('/')}/{tree}"
    assert SHA.fullmatch(tree)
    say(f"proving {branch} {tip[:7]} (tree {tree[:7]}) in {folder}")
    place = (f"set -e; d={quote(folder)}; rm -rf \"$d.tmp\"; mkdir -p \"$d.tmp\"; tar xf - -C \"$d.tmp\"; "
             f"rm -rf \"$d\"; mv \"$d.tmp\" \"$d\"")
    owner = ship.get("owner")
    if owner and owner != login:
        place += f"; chown -R {quote(owner)}: \"$d\""
    put = there(place, repo.git("archive", "--format=tar", tip, text=False))
    if put.returncode:
        raise Refused(f"could not place the tree: {put.stderr.decode(errors='replace').strip()}")
    results = []
    for user in ship["users"]:
        for command in ship["checks"]:
            line = " ".join(quote(a) for a in command)
            if user != login:
                line = f"sudo -n -u {quote(user)} env PYTHONDONTWRITEBYTECODE=1 {line}"
            done = there(f"cd {quote(folder)} && PYTHONDONTWRITEBYTECODE=1 {line}")
            tail = (done.stdout + done.stderr).decode(errors="replace").strip().splitlines()[-6:]
            results.append({"user": user, "command": command, "exit": done.returncode, "tail": tail})
            say(f"  {'ok  ' if done.returncode == 0 else 'FAIL'} as {user}: {' '.join(command)}")
            if done.returncode:
                for row in tail:
                    say("       " + row)
    # Only the newest few proofs are kept on the machine.
    keep = int(ship.get("keep", 3))
    there(f"cd {quote(str(ship['proofs']))} && ls -1t | grep -E '^[0-9a-f]{{40}}$' | tail -n +{keep + 1} | "
          "while read old; do rm -rf \"./$old\"; done")
    passed = all(r["exit"] == 0 for r in results) and bool(results)
    receipt = {"commit": tip, "tree": tree, "branch": branch, "remote": remote, "folder": folder,
               "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "passed": passed, "checks": results,
               "proof_policy": proof_policy(ship)}
    path = repo.receipt_path(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    say(f"{'proven' if passed else 'NOT proven'}: receipt {path}")
    ahead = repo.ahead(ship.get("main", "main"), tip)
    if passed and ahead:
        say(f"{branch} is {ahead} commit(s) ahead of main and proven: land it now")
    return passed


def reasons_not_to_land(repo, branch, main="main"):
    tip = repo.tip(branch)
    tree = repo.tree(tip)
    reasons = []
    if not repo.ok("merge-base", "--is-ancestor", main, tip):
        reasons.append(f"{main} is not an ancestor of {branch}: this would not be a fast-forward")
    origin = f"origin/{main}"
    if repo.ok("rev-parse", "--verify", origin) and not repo.ok("merge-base", "--is-ancestor", origin, tip):
        reasons.append(f"{origin} has commits {branch} does not: fetch and rebase first")
    receipt = repo.receipt(tree)
    if receipt is None:
        reasons.append(f"no receipt for tree {tree[:7]}: run `ship.py prove {branch}`")
    elif not receipt.get("passed"):
        failed = [f"{c['user']}: {' '.join(c['command'])}" for c in receipt["checks"] if c["exit"]]
        reasons.append(f"the proof of tree {tree[:7]} failed: {'; '.join(failed) or 'no checks ran'}")
    elif not matches_policy(repo, tip, receipt):
        reasons.append(f"proof settings changed: run `ship.py prove {branch}` for the current configuration")
    me = (repo.git("config", "user.name", check=False), repo.git("config", "user.email", check=False))
    if not all(me):
        reasons.append("this clone has no git identity (user.name and user.email): commits cannot be held to it")
    rows = repo.git("log", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e", f"{main}..{tip}")
    commits = [row.strip("\n").split("\x1f") for row in rows.split("\x1e") if row.strip()]
    if not commits:
        reasons.append(f"{branch} has nothing {main} lacks")
    for sha, an, ae, cn, ce, body in commits:
        if all(me) and ((an, ae) != me or (cn, ce) != me):
            reasons.append(f"{sha[:7]}: not authored and committed by {me[0]} <{me[1]}>")
        if TRAILER.search(body):
            reasons.append(f"{sha[:7]}: the message carries a trailer")
    words_file = repo.private() / "private-words"
    try:
        words = [w.strip() for w in words_file.read_text(encoding="utf-8").splitlines() if w.strip()]
    except OSError:
        reasons.append(f"no private list at {words_file}: create it, one name per line (empty if there are none)")
        words = []
    if words:
        text = repo.git("log", "-p", "--format=%an %ae %cn %ce%n%B", f"{main}..{tip}").lower()
        hits = sum(1 for w in words if w.lower() in text)
        if hits:
            reasons.append(f"{hits} word(s) from the private list appear in these commits or their changes")
    return tip, reasons


def land(repo, branch, say=print, main="main"):
    repo.git("fetch", "--quiet", "origin", main, check=False)
    tip, reasons = reasons_not_to_land(repo, branch, main)
    if reasons:
        raise Refused(reasons)
    count = repo.ahead(main, tip)
    repo.git("push", "origin", f"{tip}:refs/heads/{main}")  # no force: the far side refuses anything but a fast-forward
    repo.git("fetch", "--quiet", "origin", main)
    # Bring the local main along where that cannot disturb anyone's work.
    held = [w for w in repo.git("worktree", "list", "--porcelain").split("\n\n") if f"branch refs/heads/{main}" in w]
    if not held:
        repo.git("branch", "-f", main, tip)
    else:
        where = held[0].splitlines()[0].split(" ", 1)[1]
        if not Repo(where).ok("merge", "--ff-only", tip):
            say(f"local {main} in {where} was left alone: it has changes of its own")
    say(f"landed: {main} is now {tip[:7]} ({count} commit(s) from {branch})")
    return tip


def status(repo, say=print, main="main"):
    late = False
    try:
        limit = int(settings(repo, repo.tip(main)).get("max_ahead", 3))
    except Refused:
        if repo.ship_config is not None or os.path.lexists(repo.private() / "ship.toml"):
            raise
        limit = 3
    names = repo.git("for-each-ref", "--format=%(refname:short)", "refs/heads").split()
    rows = []
    for name in names:
        if name == main:
            continue
        ahead = repo.ahead(main, name)
        if not ahead:
            rows.append((name, "0", "landed"))
            continue
        tip = repo.tip(name)
        receipt = repo.receipt(repo.tree(tip))
        proven = bool(receipt and receipt.get("passed") and matches_policy(repo, tip, receipt))
        note = ("proven: land it now" if proven else "not proven" if not receipt
                else "proof settings changed" if receipt.get("passed") else "proof failed")
        if ahead > limit:
            note += f"; more than {limit} ahead"
        late = late or proven or ahead > limit
        rows.append((name, str(ahead), note))
    say(ruufd.table(rows, ("branch", "ahead of " + main, "state")) if rows else f"no branch but {main}")
    return 1 if late else 0


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(prog="ship", description="How a change reaches main.")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--ship-config", type=Path, help="trusted external TOML file with a [ship] table")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    for name in ("prove", "land"):
        sub.add_parser(name).add_argument("branch", nargs="?")
    args = parser.parse_args(argv)
    say = lambda text="": print(text, file=out, flush=True)
    repo = Repo(args.repo, args.ship_config)
    try:
        if args.command == "status":
            return status(repo, say)
        branch = args.branch or repo.current()
        if branch in ("main", "HEAD"):
            raise Refused("name the branch to ship: main is where it lands")
        if args.command == "prove":
            return 0 if prove(repo, branch, say) else 1
        land(repo, branch, say)
        return 0
    except Refused as exc:
        say("refused:")
        for reason in exc.reasons:
            say("  " + reason)
        return 1


if __name__ == "__main__":
    sys.exit(main())
