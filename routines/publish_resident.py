"""Check a resident's bounded RCC node-status source proposal. Pass --publish to land a proven change."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading


# RCC's public CLI at 0b0550a, with just the three resident-owned slots reset to their original values.
CLI_TEMPLATE = "88e9cf5ab6e813c94f966c330cfea9b7fb0d01dfb3c25f1f54776e82369eb2b5"
ORIGIN = "git@github-syn-acc:syn-acc/rcc.git"
SLOTS = (
    ("max_workers=", r"[1-8]", "8", "parallel_probes"),
    ('{"action": "status"}, ', r"1[0-2]|[1-9]", "6", "probe_seconds"),
    ('return {"ok": ', r"bool\(nodes\) and |", "", "require_nodes"),
)


class Retry(Exception):
    """A temporary failure, including a publication with an uncertain outcome."""


def git(repo, *args, data=None, check=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ForwardAgent=no -o StrictHostKeyChecking=yes -o ConnectTimeout=10")
    result = subprocess.run(["git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null",
                             "-c", "commit.gpgSign=false", "-C", str(repo), *args],
                            input=data, capture_output=True, env=env, timeout=60)
    if result.returncode and check:
        raise Retry(f"Git {args[0]} failed")
    return result


def inspect_cli(source):
    before, start, rest = source.partition("def node_status(profiles):")
    body, end, after = rest.partition("\n\ndef dispatch(args):")
    if not start or not end:
        raise ValueError("unsupported RCC CLI structure")
    policy = {}
    for prefix, pattern, default, name in SLOTS:
        slot = re.compile(re.escape(prefix) + "(" + pattern + ")")
        matches = list(slot.finditer(body))
        if len(matches) != 1:
            raise ValueError("unsupported RCC node policy")
        value = matches[0].group(1)
        policy[name] = bool(value) if name == "require_nodes" else int(value)
        body = slot.sub(lambda _: prefix + default, body)
    normalized = (before + start + body + end + after).encode()
    if hashlib.sha256(normalized).hexdigest() != CLI_TEMPLATE:
        raise ValueError("change exceeds the trusted node-status template or RCC needs a new template pin")
    return policy


def proposed_source(before, edits):
    if not isinstance(edits, list) or not 1 <= len(edits) <= 8:
        raise ValueError("expected one to eight RCC node-status edits")
    after = before
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) != {"path", "old", "new"} or edit["path"] != "rcc"
                or not isinstance(edit["old"], str) or not isinstance(edit["new"], str)
                or not edit["old"] or after.count(edit["old"]) != 1):
            raise ValueError("each source edit must match exactly one excerpt in rcc")
        after = after.replace(edit["old"], edit["new"], 1)
    if after == before:
        raise ValueError("proposal makes no change")
    return after, inspect_cli(after)


def prove(work, source, policy):
    cli, config, worker = work / "rcc", work / "profiles.json", work / "worker.sock"
    cli.write_text(source, encoding="utf-8")

    def probe(providers, expected, online=None):
        config.write_text(json.dumps({"providers": providers}))
        proc = subprocess.run([sys.executable, "-IB", str(cli), "--config", str(config), "nodes"],
                              cwd=work, env={"HOME": str(work), "PATH": "/usr/bin:/bin"},
                              capture_output=True, timeout=15)
        reply = json.loads(proc.stdout)
        if (proc.returncode != (0 if expected else 1) or reply["ok"] is not expected
                or set(reply["nodes"]) != set(providers) or
                (providers and reply["nodes"]["workstation"].get("online") is not online)):
            raise ValueError("RCC CLI node-status checks failed")

    profile = {"workstation": {"driver": "rccd", "config": {"socket": str(worker)}}}
    probe({}, not policy["require_nodes"])
    probe(profile, False, False)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(worker))
        server.listen(1)
        server.settimeout(10)
        def respond():
            client, _ = server.accept()
            with client, client.makefile("rb") as stream:
                if json.load(stream) == {"protocol": 1, "action": "status"}:
                    client.sendall(b'{"ok":true,"description":{"workload":"test"}}')
        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        probe(profile, True, True)
        thread.join(10)


def dispatch(args, proposal, digest, work):
    checkout, repo = args.checkout.resolve(strict=True), work / "repository"
    if git(checkout, "remote", "get-url", "origin").stdout.decode().strip() != ORIGIN:
        raise ValueError("expected the syn-acc RCC checkout and its scoped SSH remote")
    base = proposal.get("base_sha") if isinstance(proposal, dict) else None
    if (not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base) or
            proposal.get("repo") != "rcc"):
        raise ValueError("expected a pinned RCC source proposal")
    repo.mkdir()
    git(repo, "init", "--bare", "--quiet")
    origin = ORIGIN if args.publish else str(checkout)
    ref = "refs/heads/main" if args.publish else "refs/remotes/origin/main"
    git(repo, "fetch", "--quiet", "--no-tags", origin, f"{ref}:refs/heads/current")
    head = git(repo, "rev-parse", "refs/heads/current").stdout.decode().strip()
    if git(repo, "merge-base", "--is-ancestor", base, head, check=False).returncode:
        raise ValueError("the proposal base is outside the current RCC main history")
    if not git(repo, "ls-tree", base, "--", "rcc").stdout.startswith(b"100755 blob "):
        raise ValueError("expected the executable RCC source in the proposal base")
    before = git(repo, "show", f"{base}:rcc").stdout.decode()
    inspect_cli(before)
    source, policy = proposed_source(before, proposal.get("edits"))
    git(repo, "read-tree", base)
    blob = git(repo, "hash-object", "-w", "--stdin", data=source.encode()).stdout.decode().strip()
    git(repo, "update-index", "--cacheinfo", f"100755,{blob},rcc")
    tree = git(repo, "write-tree").stdout.decode().strip()
    marker = f"Resident-Proposal: {digest}"
    evidence = {"repo": "rcc", "base_sha": base, "tree": tree, "policy": policy}
    if head != base:
        landed = git(repo, "log", "--fixed-strings", f"--grep={marker}", "--format=%H %P %T",
                     f"{base}..{head}").stdout.decode().splitlines()
        for row in landed:
            revision, *parents, landed_tree = row.split()
            if parents == [base] and landed_tree == tree:
                return {**evidence, "status": "published", "revision": revision}
        raise ValueError("stale proposal; refresh the staged RCC source")
    prove(work, source, policy)
    evidence["checks"] = ["empty", "offline", "online"]
    if not args.publish:
        return {**evidence, "status": "verified"}
    author = git(checkout, "config", "--local", "--get", "user.name").stdout.decode().strip()
    email = git(checkout, "config", "--local", "--get", "user.email").stdout.decode().strip()
    if author not in {"syn", "syn-acc"} or not re.fullmatch(r"(?:[0-9]+\+)?syn-acc@users\.noreply\.github\.com", email):
        raise ValueError("expected the public syn-acc identity in the RCC checkout")
    message = f"Adjust RCC worker inventory diagnostics\n\n{marker}\n".encode()
    revision = git(repo, "-c", f"user.name={author}", "-c", f"user.email={email}",
                   "commit-tree", tree, "-p", base, data=message).stdout.decode().strip()
    # This lease permits only the exact, verified parent. A lost reply is reconciled
    # against the remote commit marker, parent and tree on the next invocation.
    git(repo, "push", "--porcelain", f"--force-with-lease=refs/heads/main:{base}", ORIGIN,
        f"{revision}:refs/heads/main")
    return {**evidence, "status": "published", "revision": revision}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--checkout", type=Path, required=True)
    cli.add_argument("--proposal", type=Path, required=True)
    cli.add_argument("--receipt", type=Path, required=True)
    cli.add_argument("--publish", action="store_true", help="publish to syn-acc/rcc; the default is an offline check")
    args = cli.parse_args()
    receipt = {"repo": "rcc"}
    try:
        fd = os.open(args.proposal, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("proposal must be a regular file")
            raw = stream.read(32769)
        if len(raw) > 32768:
            raise ValueError("proposal is larger than 32 KiB")
        receipt["proposal_sha256"] = hashlib.sha256(raw).hexdigest()
        # A short temp path fits macOS' Unix domain socket name limit.
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="rcc-proof-") as folder:
            receipt.update(dispatch(args, json.loads(raw), receipt["proposal_sha256"], Path(folder)))
    except (ValueError, RecursionError) as exc:
        receipt.update(status="rejected", detail=str(exc)[:300])
    except (OSError, Retry, subprocess.TimeoutExpired) as exc:
        receipt.update(status="retry", detail=type(exc).__name__)
    with tempfile.NamedTemporaryFile(mode="w", dir=args.receipt.parent, delete=False) as out:
        json.dump(receipt, out, sort_keys=True)
        out.write("\n")
    os.replace(out.name, args.receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] in {"published", "verified"} else 1


if __name__ == "__main__":
    sys.exit(main())
