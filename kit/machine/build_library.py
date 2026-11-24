#!/usr/bin/env python3
"""Collect an agent's library: redacted, bounded copies of the documents it may read.

    build_library.py --seed DIR      (as root on the host)

The soft machine never reads source paths itself; it only ever sees what this script wrote into
DIR/library/. What goes in is listed in DIR/library.json:

    {"authored": {"key": ["Title", "project"]},            papers in DIR/library_src/key.md
     "sources":  {"key": ["Title", "project", "/path"]},   documents copied from elsewhere on the host
     "public":   ["key", ...]}                             what it may repeat, and what its hands may read

RULE (Syn, 2026-11-19, after Ruu recited its own design pack in the channel): an agent only gets
to know what it may say. A model cannot be trusted to keep a paper secret once it has read it, so
internal papers do not go in at all: no design pack, no brand notes, no design ledgers, no runner
internals, no operations notes. Anything listed here and not public is still read; what the agent
learns from it is marked internal and the machine forbids reciting it. Keep that list to the one
paper it cannot work without (for Ruu, the source of its orders).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed as seeds  # noqa: E402

CHUNK, MAX_CHUNKS = 8000, 2


def redact(text):
    lines = []
    for line in text.splitlines():
        if re.search(r"token|password|passwd|secret|api[_-]?key|credential|bearer", line, re.I):
            lines.append("[line withheld]")
        else:
            lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "[address]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", text)
    text = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "[redacted]", text)
    text = re.sub(r"(https?://[^\s)?]+)\?[^\s)]*", r"\1", text)
    text = re.sub(r"(?<![\w.:/])/(?:data|srv|opt|etc|root|usr|var|home|run)/[^\s`'\")]*", "[path]", text)
    return text


def build(folder, workspace_data=None):
    folder = Path(folder)
    spec = json.loads((folder / "library.json").read_text(encoding="utf-8"))
    public, out = set(spec.get("public") or []), folder / "library"
    out.mkdir(exist_ok=True)
    for old in out.glob("*.md"):
        old.unlink()
    manifest = {}

    def add(key, title, project, text):
        text = redact(text).strip()
        parts = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)][:MAX_CHUNKS] or [""]
        for n, part in enumerate(parts, 1):
            k = key if len(parts) == 1 else f"{key}.{n}"
            (out / f"{k}.md").write_text(part, encoding="utf-8")
            manifest[k] = {"title": title + (f" (part {n} of {len(parts)})" if len(parts) > 1 else ""),
                           "project": project, "chars": len(part), "truncated": len(text) > CHUNK * MAX_CHUNKS,
                           "public": key in public}

    for key, (title, project) in (spec.get("authored") or {}).items():
        add(key, title, project, (folder / "library_src" / f"{key}.md").read_text(encoding="utf-8"))
    for key, (title, project, path) in (spec.get("sources") or {}).items():
        p = Path(path)
        if not p.is_file():
            print("missing", key, path, file=sys.stderr)
            continue
        add(key, title, project, p.read_text(encoding="utf-8", errors="replace"))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for k, v in manifest.items():
        print(f"{k:32} {v['chars']:6} {'public  ' if v['public'] else 'INTERNAL'} {v['title']}")
    # Seed the hands workspace with the public documents only, read-only.
    data = Path(workspace_data or f"/data/{seeds.load(folder)['id']}-hands/workspace/data")
    if data.parent.is_dir():
        data.mkdir(exist_ok=True)
        for old in data.glob("*.md"):
            old.unlink()
        for k, v in manifest.items():
            if v["public"]:
                (data / f"{k}.md").write_text((out / f"{k}.md").read_text(encoding="utf-8"), encoding="utf-8")
                (data / f"{k}.md").chmod(0o644)
        data.chmod(0o755)
        print("seeded", sorted(p.name for p in data.glob("*.md")))
    return manifest


if __name__ == "__main__":
    build(seeds.locate())
