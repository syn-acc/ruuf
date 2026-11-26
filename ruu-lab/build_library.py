#!/usr/bin/env python3
"""Collect Ruu's library: redacted, bounded copies of documents it may read.

Run as root on uk. The soft machine never reads source paths itself; it only ever
sees what this script wrote into library/. Add a source here to let Ruu read it.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "library"
CHUNK, MAX_CHUNKS = 8000, 2

# RULE (Syn, 2026-11-19, after Ruu recited its own design pack in the channel):
# Ruu only gets to know what it may say. A model cannot be trusted to keep a
# paper secret once it has read it, so internal papers do not go in at all: no
# design pack, no brand notes, no game design ledgers, no runner internals.
# The one exception is from-above, the source of its orders; what Ruu learns
# from it is marked internal and the soft machine forbids reciting it.
AUTHORED = {
    "scru-architecture": ("How SCRU fits together", "scru"),
    "framework-work": ("Contributing to RUUF and RCC", "scru"),
    "from-above": ("What has been said from above", "orders"),
    "atlas": ("Atlas of SCRU projects", "scru"),
    "openrsi-brief": ("OpenRSI: the brief", "openrsi"),
}
# Only public documents are copied into the hands workspace as data/.
PUBLIC = {"scru-architecture", "framework-work", "atlas", "lore", "scrucraft-guidance", "scrucraft-release-status", "openrsi-brief"}
SOURCES = {
    "lore": ("Lore of the Singularity Cycle (game document)", "singularity-cycle", "/data/syn.gl/game/docs/LORE.md"),
    "scrucraft-guidance": ("scrucraft: connect your agent", "scrucraft", "/data/minecraft-syn-smp/public/guidance.md"),
    "scrucraft-release-status": ("scrucraft release status", "scrucraft", "/data/minecraft-syn-smp/public/docs/release-status.md"),
}


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


def main():
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.md"):
        old.unlink()
    manifest = {}

    def add(key, title, project, text):
        text = redact(text).strip()
        parts = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)][:MAX_CHUNKS] or [""]
        for n, part in enumerate(parts, 1):
            k = key if len(parts) == 1 else f"{key}.{n}"
            (OUT / f"{k}.md").write_text(part, encoding="utf-8")
            manifest[k] = {"title": title + (f" (part {n} of {len(parts)})" if len(parts) > 1 else ""),
                           "project": project, "chars": len(part), "truncated": len(text) > CHUNK * MAX_CHUNKS,
                           "public": key in PUBLIC}

    for key, (title, project) in AUTHORED.items():
        add(key, title, project, (HERE / "library_src" / f"{key}.md").read_text(encoding="utf-8"))
    for key, (title, project, path) in SOURCES.items():
        p = Path(path)
        if not p.is_file():
            print("missing", key, path, file=sys.stderr)
            continue
        add(key, title, project, p.read_text(encoding="utf-8", errors="replace"))
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for k, v in manifest.items():
        print(f"{k:32} {v['chars']:6} {'public  ' if v['public'] else 'INTERNAL'} {v['title']}")
    # Seed the hands workspace with the public documents only, read-only.
    data = Path("/data/ruu-hands/workspace/data")
    if data.parent.is_dir():
        data.mkdir(exist_ok=True)
        for old in data.glob("*.md"):
            old.unlink()
        for k, v in manifest.items():
            if v["public"]:
                (data / f"{k}.md").write_text((OUT / f"{k}.md").read_text(encoding="utf-8"), encoding="utf-8")
                (data / f"{k}.md").chmod(0o644)
        data.chmod(0o755)
        print("seeded", sorted(p.name for p in data.glob("*.md")))


if __name__ == "__main__":
    main()
