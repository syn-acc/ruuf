#!/usr/bin/env bash
# Rebuild Ruu's Hermes runtime: upstream at the pinned commit, plus the patch and new files.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
dest="${1:-runtime}"
git clone https://github.com/NousResearch/hermes-agent.git "$dest"
git -C "$dest" checkout "$(cat "$here/UPSTREAM_COMMIT")"
git -C "$dest" apply "$here"/patches/*.patch
cp -r "$here/overlay/." "$dest/"
