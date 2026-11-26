"""Stage public sources for the resident, or check its text replacements and export a patch."""

import argparse
import difflib
import fcntl
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


SURFACES = {
    "ruuf": ("README.md", "ruu-lab/library_src/atlas.md", "ruu-lab/library_src/openrsi-brief.md",
             "plugins/discord/voice/idle/variation.py"),
    "rcc": ("README.md", "PROVIDERS.md", "rcc"),
}
LIMIT = 256 * 1024
DIR = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY
READ = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_RDONLY
SECRET = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|\b(?:gh[pousr]_|github_pat_|sk-proj-)[A-Za-z0-9_-]{20,}")


def git(checkout, *args, data=None, index=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    proc = subprocess.run(["git", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false",
                           "-c", "core.hooksPath=/dev/null", "-C", str(checkout), *args],
                          input=data, capture_output=True, env=env)
    if proc.returncode:
        raise ValueError(f"Git {args[0]} check failed")
    return proc.stdout


def public_sources(repo, checkout):
    if Path(os.fsdecode(git(checkout, "rev-parse", "--show-toplevel")).strip()).resolve() != checkout:
        raise ValueError("checkout must be the repository root")
    origin = git(checkout, "config", "--get", "remote.origin.url").decode().strip().removesuffix(".git")
    prefixes = ("https://github.com/", "git@github.com:", "git@github-syn-acc:", "ssh://git@github.com/")
    if origin not in {prefix + "syn-acc/" + repo for prefix in prefixes}:
        raise ValueError(f"checkout origin must be syn-acc/{repo}")
    if git(checkout, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("checkout must be clean")
    sha = git(checkout, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    git(checkout, "merge-base", "--is-ancestor", sha, "refs/remotes/origin/main")
    files = {}
    for path in SURFACES[repo]:
        entry = git(checkout, "ls-tree", "-l", sha, "--", path).split()
        if not entry:
            continue
        if entry[0] not in (b"100644", b"100755") or entry[1] != b"blob" or int(entry[3]) > LIMIT:
            raise ValueError(f"not a bounded regular Git source: {path}")
        text = git(checkout, "cat-file", "blob", entry[2].decode()).decode("utf-8")
        if "\0" in text or SECRET.search(text):
            raise ValueError(f"unexpected binary or credential material in {path}")
        files[path] = text
    if not files:
        raise ValueError("no approved sources in this revision")
    return sha, files


def trusted_dir(path):
    path.mkdir(mode=0o755, exist_ok=True)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022 or info.st_mode & 0o005 != 0o005):
        raise ValueError(f"unsafe operator-owned directory: {path}")


def read_regular(dir_fd, name, owner, readonly=False):
    before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"expected a regular file: {name}")
    fd = os.open(name, READ, dir_fd=dir_fd)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(fd)
        if (not os.path.samestat(before, info) or not stat.S_ISREG(info.st_mode)
                or info.st_uid != owner or info.st_nlink != 1
                or (readonly and (info.st_mode & 0o222 or not info.st_mode & 0o004))):
            raise ValueError(f"unsafe file: {name}")
        value = stream.read(LIMIT + 1)
        if len(value) > LIMIT:
            raise ValueError(f"file exceeds {LIMIT} bytes")
        return value


def verify_snapshot(snapshot, files):
    expected = {Path(name) for name in files}
    directories = {p for name in expected for p in name.parents if p != Path(".")}
    seen = {p.relative_to(snapshot) for p in snapshot.rglob("*")}
    if seen != expected | directories:
        raise ValueError("snapshot has unexpected or missing entries")
    for path in directories:
        trusted_dir(snapshot / path)
    for name, content in files.items():
        if read_regular(None, snapshot / name, os.geteuid(), readonly=True) != content.encode():
            raise ValueError(f"existing snapshot differs from Git: {name}")


def stage(work_fd, repo, sha, files):
    # The resident owns the workspace. Anchor all writes below its verified operator-owned data inode.
    try:
        os.mkdir("data", 0o755, dir_fd=work_fd)
    except FileExistsError:
        pass
    data_fd = os.open("data", DIR, dir_fd=work_fd)
    original = os.open(".", DIR)
    try:
        os.fchdir(data_fd)
        trusted_dir(Path("."))
        fcntl.flock(data_fd, fcntl.LOCK_EX)
        parent = Path("framework") / repo
        trusted_dir(parent.parent)
        trusted_dir(parent)
        snapshot = parent / sha
        if snapshot.exists() or snapshot.is_symlink():
            trusted_dir(snapshot)
            verify_snapshot(snapshot, files)
        else:
            with tempfile.TemporaryDirectory(dir=parent) as scratch:
                build = Path(scratch) / "source"
                build.mkdir(mode=0o755)
                for name, text in files.items():
                    target = build / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(text.encode())
                    target.chmod(0o444)
                for path in (build, *(p for p in build.rglob("*") if p.is_dir())):
                    path.chmod(0o755)
                build.rename(snapshot)
        current = parent / "CURRENT"
        if current.exists() or current.is_symlink():
            read_regular(None, current, os.geteuid(), readonly=True)
        with tempfile.TemporaryDirectory(dir=parent) as scratch:
            pointer = Path(scratch) / "CURRENT"
            pointer.write_bytes((sha + "\n").encode())
            pointer.chmod(0o444)
            pointer.replace(current)
        if not os.path.samestat(os.fstat(data_fd), os.stat("data", dir_fd=work_fd, follow_symlinks=False)):
            raise ValueError("workspace data directory changed during staging")
    finally:
        os.fchdir(original)
        os.close(original)
        os.close(data_fd)


def proposal_patch(repo, sha, files, proposal):
    if not isinstance(proposal, dict) or proposal.get("repo") != repo or proposal.get("base_sha") != sha:
        raise ValueError("proposal repository or base SHA differs from checkout")
    edits = proposal.get("edits")
    if not isinstance(edits, list) or not 1 <= len(edits) <= 32:
        raise ValueError("proposal must contain 1 to 32 edits")
    updated = files.copy()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"path", "old", "new"} or not all(isinstance(v, str) for v in edit.values()):
            raise ValueError("each edit requires path, old and new strings")
        path, old, new = (edit[k] for k in ("path", "old", "new"))
        source = updated.get(path, "")
        start = source.find(old)
        if path not in files or not old or start < 0 or source.find(old, start + 1) >= 0 or "\0" in new or SECRET.search(new):
            raise ValueError("edit must replace one unique literal excerpt in an approved text file")
        updated[path] = updated[path].replace(old, new, 1)
        if len(updated[path].encode()) > LIMIT:
            raise ValueError("modified source exceeds size limit")
    lines = []
    for path, before in files.items():
        for line in difflib.unified_diff(io.StringIO(before).readlines(), io.StringIO(updated[path]).readlines(),
                                         "a/" + path, "b/" + path):
            lines.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    if not lines:
        raise ValueError("proposal produces no changes")
    return "".join(lines).encode()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    for action in ("stage", "check"):
        cmd = commands.add_parser(action)
        cmd.add_argument("--repo", required=True, choices=SURFACES)
        cmd.add_argument("--checkout", required=True, type=Path)
        cmd.add_argument("--workspace", required=True, type=Path)
        if action == "check":
            cmd.add_argument("--output", required=True, type=Path)
    args = cli.parse_args()
    checkout, workspace = args.checkout.resolve(strict=True), args.workspace.resolve(strict=True)
    if args.workspace.is_symlink() or workspace.is_relative_to(checkout) or checkout.is_relative_to(workspace):
        raise ValueError("checkout and real workspace must be separate")
    sha, files = public_sources(args.repo, checkout)
    fd = os.open(workspace, DIR)
    try:
        if args.command == "stage":
            previous_mask = os.umask(0o022)
            try:
                stage(fd, args.repo, sha, files)
            finally:
                os.umask(previous_mask)
            print(f"Staged {len(files)} files at data/framework/{args.repo}/{sha}")
            return
        name = f"framework-{args.repo}-proposal.json"
        proposal = json.loads(read_regular(fd, name, os.fstat(fd).st_uid))
    finally:
        os.close(fd)
    patch = proposal_patch(args.repo, sha, files, proposal)
    store = Path(os.fsdecode(git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir")).strip())
    output = args.output.parent.resolve(strict=True) / args.output.name
    if any(output.is_relative_to(place) for place in (workspace, checkout, store)):
        raise ValueError("output must be outside the workspace and checkout")
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index"
        git(checkout, "read-tree", sha, index=index)
        git(checkout, "apply", "--cached", "--check", "--whitespace=nowarn", "-", index=index, data=patch)
    with tempfile.NamedTemporaryFile(dir=output.parent) as ready:
        ready.write(patch)
        ready.flush()
        os.link(ready.name, output)  # Publish completely or fail if anything already has this name.
    print(f"Patch applies to {args.repo} {sha}: {output}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RecursionError) as exc:
        print(f"framework_change: {exc}", file=sys.stderr)
        sys.exit(1)
