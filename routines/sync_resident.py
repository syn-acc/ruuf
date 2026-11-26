"""Poll one resident RCC proposal from an operator's Mac; recover publication and UK installation."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import runpy
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = Path("/var/lib/ruuf-framework")
WORKSPACE = Path("/data/ruu-hands/workspace")
CHECKOUT = ROOT / "rcc"
TARGET = Path("/usr/local/bin/rcc")
ORIGIN = "https://github.com/syn-acc/rcc.git"
INSTALL_SHA256 = "288d268c537c6570cd12ef36a69ca42dfb3b16b2d7e7400c772f15ee26c87ac9"
PROPOSAL = "framework-rcc-proposal.json"
LIMIT = 32 * 1024
DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
REFUSALS = {"proposal_rejected", "unsafe_proposal", "stale_base", "outside_node_policy", "excerpt_mismatch", "no_change"}
PUBLISHER_REASONS = {
    "stale proposal; refresh the staged RCC source": "stale_base",
    "the proposal base is outside the current RCC main history": "stale_base",
    "change exceeds the trusted node-status template or RCC needs a new template pin": "outside_node_policy",
    "unsupported RCC node policy": "outside_node_policy",
    "each source edit must match exactly one excerpt in rcc": "excerpt_mismatch",
    "proposal makes no change": "no_change",
}
SHA = re.compile(r"[a-f0-9]{40}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value):
    return isinstance(value, str) and bool(SHA.fullmatch(value))


def owned_dir(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022,
            "unsafe operator directory")


def atomic(path, data, mode=0o600):
    # Only used under locked operator-controlled directories. Never follow or replace unexpected objects.
    if os.path.lexists(path):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                and not info.st_mode & 0o022, "unsafe previous state")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            os.replace(temp, path)
            fd = os.open(path.parent, DIR)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temp.unlink(missing_ok=True)


def save(path, obj, mode=0o600):
    atomic(path, json.dumps(obj, sort_keys=True).encode() + b"\n", mode)


def receive():
    uid = pwd.getpwnam("ruu-hands").pw_uid
    require(os.geteuid() == uid and uid != 0, "receive requires the resident UID")
    fd = os.open(WORKSPACE, DIR)
    try:
        require(os.fstat(fd).st_uid == uid, "unexpected workspace owner")
        try:
            info = os.stat(PROPOSAL, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return 3
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1 or info.st_size > LIMIT:
            return 4
        file_fd = os.open(PROPOSAL, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as stream:
            current = os.fstat(file_fd)
            if not os.path.samestat(current, info) or current.st_uid != uid or current.st_nlink != 1:
                return 4
            contents = stream.read(LIMIT + 1)
            after = os.fstat(file_fd)
            require(current.st_ctime_ns == after.st_ctime_ns and len(contents) == after.st_size, "proposal changed during read")
            if len(contents) > LIMIT:
                return 4
        sys.stdout.buffer.write(contents)
        return 0
    finally:
        os.close(fd)


def receipt(status, base, revision, reason):
    require(status in {"published", "active", "refused"}, "unknown receipt")
    require(base is None or sha(base), "bad base")
    if status == "refused":
        require(revision is None and reason in REFUSALS, "bad refusal")
    else:
        require(sha(base) and sha(revision) and base != revision, "bad publication")
        require(reason in ({"installed"} if status == "active" else {"awaiting_install", "newer_release_active"}), "bad release state")
    return dict(repo="rcc", base_sha=base, revision=revision, status=status, reason=reason)


def git(*args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    p = subprocess.run(["/usr/bin/git", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false",
                        "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=", "-C", CHECKOUT, *args],
                       stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=90)
    require(p.returncode == 0, "release Git check failed")
    return p.stdout


def bundle(revision):
    # Mirror the pinned installer's digest using only committed payload bytes, including executable bits.
    entries = {}
    for row in git("ls-tree", "-r", "-z", revision, "--", "rcc", "rccd", "plugins").split(b"\0"):
        if not row:
            continue
        header, name = row.split(b"\t", 1)
        if any(part.startswith(b".") or part == b"__pycache__" for part in name.split(b"/")) or name.endswith((b".pyc", b".pyo")):
            continue
        mode, kind, oid = header.split()
        require(mode in {b"100644", b"100755"} and kind == b"blob", "unexpected bundle entry")
        entries[name] = (mode == b"100755", oid)
    require(b"rcc" in entries and b"rccd" in entries, "incomplete bundle")
    h = hashlib.sha256(b"rcc-bundle-v1\x00")
    for name, (executable, oid) in sorted(entries.items()):
        data = git("cat-file", "blob", oid.decode())
        h.update(len(name).to_bytes(8, "big") + name + bytes([executable]) + len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


def activate(base, rev):
    owned_dir(CHECKOUT)
    marker = ROOT / "qualified-revision"
    info = marker.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o022,
            "unsafe qualification marker")
    qualified = marker.read_text("ascii").strip()
    require(sha(qualified), "unqualified source baseline")
    require(git("config", "--get", "remote.origin.url").decode().strip() == ORIGIN, "unexpected release origin")
    require(not git("status", "--porcelain=v1", "--untracked-files=all", "--ignored"), "release checkout must be clean")
    head = git("rev-parse", "--verify", "HEAD").decode().strip()
    git("fetch", "--no-tags", "--no-recurse-submodules", ORIGIN, "+refs/heads/main:refs/remotes/origin/main")
    git("merge-base", "--is-ancestor", rev, "refs/remotes/origin/main")
    require(git("rev-list", "--parents", "-1", rev).decode().split() == [rev, base], "expected one direct descendant")
    require(git("diff-tree", "-r", "--no-renames", "--no-commit-id", "--name-status", base, rev) == b"M\trcc\n",
            "candidate changed files outside the validated client")
    require(git("ls-tree", rev, "--", "rcc").startswith(b"100755 blob "), "client must remain executable")
    inspect = runpy.run_path(str(ROOT / "tools/publish_resident.py"))["inspect_cli"]
    for commit in (base, rev):
        inspect(git("cat-file", "blob", commit + ":rcc").decode())
    if qualified not in {base, rev}:
        # A restarted publisher may rediscover a previously landed change after a later activation.
        require(head == qualified, "release checkout has a different revision")
        git("merge-base", "--is-ancestor", rev, qualified)
        expected = TARGET.parent.parent / "lib" / "rcc" / bundle(qualified)
        require(all(binary.is_symlink() and binary.resolve(strict=True) == expected / binary.name
                    for binary in (TARGET, TARGET.with_name("rccd"))), "unexpected active release")
        return receipt("published", base, rev, "newer_release_active")
    require(head in {base, rev} and (qualified != rev or head == rev), "release checkout has a different revision")
    old, new = bundle(base), bundle(rev)
    versions = TARGET.parent.parent / "lib" / "rcc"
    for binary in (TARGET, TARGET.with_name("rccd")):
        require(binary.is_symlink() and binary.resolve(strict=True) in
                {versions / digest / binary.name for digest in (old, new)}, "installed link differs from qualified release")
    if head != rev:
        git("merge", "--ff-only", "--no-edit", "--no-stat", rev)
    installer = CHECKOUT / "install.py"
    require(git("rev-parse", "HEAD").decode().strip() == rev and
            hashlib.sha256(installer.read_bytes()).hexdigest() == INSTALL_SHA256, "installer pin mismatch")
    run = subprocess.run([sys.executable, "-IB", installer, "--target", TARGET, "--expect-digest", new],
                         stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
    require(run.returncode == 0, "pinned installation failed")
    for binary in (TARGET, TARGET.with_name("rccd")):
        require(binary.resolve(strict=True) == versions / new / binary.name, "release switch incomplete")
    stage = subprocess.run([sys.executable, "-IB", ROOT / "tools/framework_change.py", "stage", "--repo", "rcc",
                            "--checkout", CHECKOUT, "--workspace", WORKSPACE],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    require(stage.returncode == 0, "release snapshot staging failed")
    atomic(marker, (rev + "\n").encode(), 0o644)
    return receipt("active", base, rev, "installed")


def remote_feedback(action):
    require(os.geteuid() == 0, "release operation requires the operator")
    owned_dir(ROOT)
    require(ROOT.resolve() == ROOT, "unexpected release state path")
    fd = os.open(ROOT, DIR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        wire = sys.stdin.buffer.read(2049)
        require(len(wire) <= 2048, "oversized receipt")
        msg = json.loads(wire)
        item = receipt(msg["status"], msg["base_sha"], msg["revision"], msg["reason"])
        require(item == msg, "unexpected receipt fields")
        path = ROOT / "framework-result.json"
        if action == "notify":
            require(item["status"] == "refused", "expected a refusal")
        else:
            require(item["status"] == "published" and item["reason"] == "awaiting_install", "expected a publication")
            # Keep a completed delivery intact if SSH dropped after its write.
            active = receipt("active", item["base_sha"], item["revision"], "installed")
            if os.path.lexists(path):
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                        and not info.st_mode & 0o022, "unsafe previous receipt")
            if not path.exists() or json.loads(path.read_bytes()) != active:
                save(path, item, 0o644)
            item = activate(item["base_sha"], item["revision"])
        save(path, item, 0o644)
    finally:
        os.close(fd)
    return 0


def ssh(host, action, message=None):
    who = "ruu-hands" if action == "receive" else "root"
    command = f"/usr/bin/sudo -n -u {who} /usr/bin/python3 -IB {ROOT}/tools/sync_resident.py {action}"
    return subprocess.run(["/usr/bin/ssh", "-oBatchMode=yes", "-oForwardAgent=no", "-oClearAllForwardings=yes",
                           "-oStrictHostKeyChecking=yes", "-oConnectTimeout=8", host, command],
                          input=b"" if message is None else json.dumps(message).encode(),
                          capture_output=True, timeout=240)


def tick(checkout, directory, host):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,79}", host), "invalid SSH alias")
    require(not directory.is_symlink(), "expected a private state directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = directory.resolve()
    owned_dir(directory)
    require(not directory.stat().st_mode & 0o077 and not directory.is_relative_to(checkout), "expected private external state")
    fd = os.open(directory, DIR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        path = directory / "sync-state.json"
        state = json.loads(path.read_bytes()) if path.exists() else {}
        if state.get("phase") not in {"publish", "deliver"}:
            found = ssh(host, "receive")
            if found.returncode == 3:
                return 0
            require(found.returncode in {0, 4}, "resident transport unavailable")
            unsafe = found.returncode == 4
            raw = found.stdout
            require(len(raw) <= LIMIT, "unexpected transport length")
            fingerprint = "unsafe" if unsafe else hashlib.sha256(raw).hexdigest()
            if state.get("id") == fingerprint:
                return 0
            try:
                parsed = json.loads(raw)
                base = parsed.get("base_sha") if isinstance(parsed, dict) else None
            except (ValueError, UnicodeError, RecursionError):
                base = None
            base = base if sha(base) else None
            state = {"id": fingerprint, "phase": "publish", "base": base}
            if unsafe or base is None:
                state.update(phase="deliver", receipt=receipt("refused", base, None, "unsafe_proposal" if unsafe else "proposal_rejected"))
            else:
                state["proposal"] = base64.b64encode(raw).decode("ascii")
            save(path, state)
        if state["phase"] == "publish":
            with tempfile.TemporaryDirectory(dir=directory) as scratch:
                proposal = Path(scratch) / PROPOSAL
                proposal.write_bytes(base64.b64decode(state["proposal"], validate=True))
                result = Path(scratch) / "publisher.json"
                subprocess.run([sys.executable, "-IB", HERE / "publish_resident.py", "--checkout", checkout,
                                "--proposal", proposal, "--receipt", result, "--publish"],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
                require(result.is_file(), "publisher did not return a receipt")
                outcome = json.loads(result.read_bytes())
            require(outcome.get("repo") == "rcc" and outcome.get("proposal_sha256") == state["id"], "publisher receipt mismatch")
            if outcome.get("status") == "published":
                require(outcome.get("base_sha") == state["base"], "publisher base mismatch")
                message = receipt("published", state["base"], outcome.get("revision"), "awaiting_install")
            elif outcome.get("status") == "rejected":
                detail = outcome.get("detail")
                require(detail not in {"expected the public syn-acc identity in the RCC checkout",
                                       "expected the syn-acc RCC checkout and its scoped SSH remote"}, "publisher configuration needs attention")
                message = receipt("refused", state["base"], None, PUBLISHER_REASONS.get(detail, "proposal_rejected"))
            else:
                raise ValueError("publisher requested a retry")
            state = {"id": state["id"], "phase": "deliver", "receipt": message}
            save(path, state)
        message = state["receipt"]
        result = ssh(host, "activate" if message["status"] == "published" else "notify", message)
        require(result.returncode == 0, "release delivery pending")
        save(path, {"id": state["id"], "phase": "done"})
        print("delivered" if message["status"] == "published" else "refused")
        return 0
    finally:
        os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    cmd = sub.add_parser("tick")
    cmd.add_argument("--checkout", type=Path, required=True)
    cmd.add_argument("--state-dir", type=Path, required=True)
    cmd.add_argument("--host", default="uk")
    for name in ("receive", "activate", "notify"):
        sub.add_parser(name)
    args = p.parse_args()
    if args.action == "tick":
        return tick(args.checkout.resolve(strict=True), args.state_dir, args.host)
    if args.action == "receive":
        return receive()
    return remote_feedback(args.action)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, RecursionError, subprocess.SubprocessError) as exc:
        # Provider, Git and resident output may be sensitive. Only our own fixed diagnostics are printable.
        print(str(exc) if type(exc) is ValueError and exc.args and isinstance(exc.args[0], str)
              and re.fullmatch(r"[A-Za-z ]{1,80}", exc.args[0]) else "framework sync could not complete", file=sys.stderr)
        sys.exit(1)
