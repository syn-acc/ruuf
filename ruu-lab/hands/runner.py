#!/usr/bin/python3
"""Ruu's hands: run ONE job inside the systemd sandbox.

systemd accepts a connection on /run/ruu-hands.sock and starts this script as
the unprivileged user ruu-hands with the socket as stdin/stdout. It reads one
JSON job, writes the files into the workspace, runs one command, and answers
with one JSON result. Every limit that matters (no network, no view of the
server, memory, time, disk) is enforced by the unit and the mount, not by this
script; this script only keeps the job tidy.
"""
import base64
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

WORKSPACE = "/data/ruu-hands/workspace"
MAX_JOB = 400_000
PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]{0,119}$")
SUBMISSION = "openrsi/submission.json"
PUBLIC_TYPES = {"png", "gif", "jpg", "jpeg", "svg", "txt", "md", "html", "json", "csv"}

# Four originals plus four previews and a submission must fit the 6 MB socket reply.
PREVIEW_LIMIT = 400_000


def svg_preview(source):
    """Render the captured bytes inside the existing hands sandbox."""
    # GTK's format detection misses <ns0:svg> emitted by ElementTree.
    if re.search(rb"<[A-Za-z_][A-Za-z0-9_.-]*:svg[\s>]", source):
        try:
            if b"<!DOCTYPE" in source.upper():
                return None
            root = ET.fromstring(source)
            if root.tag != "{http://www.w3.org/2000/svg}svg":
                return None
            ET.register_namespace("", "http://www.w3.org/2000/svg")
            source = ET.tostring(root, encoding="utf-8")
        except (ET.ParseError, ValueError, RecursionError, LookupError):
            return None
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8",
           "FONTCONFIG_FILE": "/opt/ruu-hands/preview-fonts.conf"}
    try:
        for size in (1200, 640):
            with tempfile.TemporaryFile(dir="/tmp") as png:
                proc = subprocess.run(
                    ["/usr/bin/gdk-pixbuf-thumbnailer", "-s", str(size), "/dev/stdin", "/dev/stdout"],
                    input=source, stdout=png, stderr=subprocess.DEVNULL, timeout=4, env=env,
                )
                if proc.returncode:
                    return None
                png.seek(0)
                data = png.read(PREVIEW_LIMIT + 1)
                if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) <= PREVIEW_LIMIT:
                    return base64.b64encode(data).decode("ascii")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def answer(**result):
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    sys.exit(0)


def tail(path, limit):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", "replace"), size
    except OSError:
        return "", 0


def fingerprint(path):
    try:
        st = os.stat(os.path.join(WORKSPACE, path))
        return st.st_mtime_ns, st.st_size, st.st_ino
    except OSError:
        return None


def main():
    raw = sys.stdin.buffer.read(MAX_JOB + 1)
    if len(raw) > MAX_JOB:
        answer(ok=False, error="job too large")
    try:
        job = json.loads(raw)
        files, command = job.get("files") or [], job.get("command") or ""
        assert isinstance(files, list) and len(files) <= 8 and isinstance(command, str) and len(command) <= 2000
    except Exception:
        answer(ok=False, error="bad job")
    os.chdir(WORKSPACE)
    began = time.time() - 1
    before = fingerprint(SUBMISSION)  # a submission left by an earlier job is never scored again
    written = []
    for item in files:
        path, content = str(item.get("path", "")), item.get("content", "")
        if (not PATH_RE.match(path) or ".." in path.split("/") or path.split("/")[0] == "data"
                or not isinstance(content, str) or len(content) > 60000):
            answer(ok=False, error=f"refused file: {path[:60]}")
        full = os.path.realpath(os.path.join(WORKSPACE, path))
        if not full.startswith(WORKSPACE + "/"):
            answer(ok=False, error="path leaves the workspace")
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as handle:
                handle.write(content)
        except OSError as exc:
            answer(ok=False, error=f"could not write {path[:60]}: {exc.strerror}")
        written.append(path)
    result = {"ok": True, "written": written, "exit": None, "secs": 0.0, "timed_out": False,
              "stdout": "", "stderr": "", "stdout_bytes": 0}
    if command.strip():
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": WORKSPACE, "LANG": "C.UTF-8",
               "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
               "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "TMPDIR": "/tmp"}
        timeout = max(1, min(int(job.get("timeout") or 100), 110))
        started = time.monotonic()
        with open("/tmp/out", "wb") as out, open("/tmp/err", "wb") as err:
            try:
                proc = subprocess.Popen(["/bin/bash", "-c", command], cwd=WORKSPACE, env=env, stdout=out,
                                        stderr=err, stdin=subprocess.DEVNULL, start_new_session=True)
            except (ValueError, OSError) as exc:
                answer(ok=False, error=f"could not start command: {type(exc).__name__}")
            try:
                result["exit"] = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                proc.wait()
        result["secs"] = round(time.monotonic() - started, 2)
        result["stdout"], result["stdout_bytes"] = tail("/tmp/out", 4000)
        result["stderr"], _ = tail("/tmp/err", 2000)
    listing, total = [], 0
    for root, dirs, names in os.walk(WORKSPACE):
        dirs[:] = [d for d in dirs if d not in ("lost+found", "__pycache__")]
        for name in names:
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            total += size
            listing.append((os.path.getmtime(full), os.path.relpath(full, WORKSPACE), size))
    listing.sort(reverse=True)
    stat = os.statvfs(WORKSPACE)
    # Media: files this job left in public/ go back to the host, which shows them to the room.
    result["public"] = []
    for mtime, path, size in listing:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if (path.startswith("public/") and mtime >= began and 0 < size <= 600_000 and ext in PUBLIC_TYPES
                and len(result["public"]) < 4):
            try:
                with open(os.path.join(WORKSPACE, path), "rb") as handle:
                    source = handle.read(600_001)
                if not 0 < len(source) <= 600_000:
                    continue
                item = {"path": path, "b64": base64.b64encode(source).decode("ascii")}
                if ext == "svg":
                    item["preview_png"] = svg_preview(source)
                result["public"].append(item)
            except OSError:
                pass
    # OpenRSI: a submission written by THIS job goes back to the host, which holds the labels and
    # does the scoring. Nothing in here can compute the score, so nothing in here can invent it.
    after = fingerprint(SUBMISSION)
    if after and after != before and after[1] <= 200_000 and result["exit"] == 0:
        try:
            with open(os.path.join(WORKSPACE, SUBMISSION), "rb") as handle:
                result["submission"] = handle.read().decode("utf-8", "replace")
        except OSError:
            pass
    result["workspace"] = [{"path": p, "bytes": s} for _, p, s in listing[:60]]
    result["workspace_files"] = len(listing)
    result["workspace_bytes"] = total
    result["disk_free_bytes"] = stat.f_bavail * stat.f_frsize
    answer(**result)


if __name__ == "__main__":
    main()
