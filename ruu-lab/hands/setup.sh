#!/bin/bash
# One-time setup of Ruu's hands on uk. Run as root from the directory holding
# runner.py and the two unit files. Safe to run again.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BASE=/data/ruu-hands
WS=$BASE/workspace
IMG=$BASE/workspace.img

id ruu-hands >/dev/null 2>&1 || useradd --system --home-dir "$WS" --no-create-home --shell /usr/sbin/nologin ruu-hands
mkdir -p "$WS" /opt/ruu-hands
chmod 755 "$BASE"

# The workspace is its own 1 GB filesystem, so nothing Ruu runs can fill /data.
if [ ! -f "$IMG" ]; then
  truncate -s 1G "$IMG"
  mkfs.ext4 -q -F -m 0 -L ruu-hands "$IMG"
fi
chmod 600 "$IMG"
if ! grep -q "$IMG" /etc/fstab; then
  cp -a /etc/fstab "/etc/fstab.pre-ruu-hands"
  echo "$IMG $WS ext4 loop,nosuid,nodev,nofail 0 0" >> /etc/fstab
fi
mountpoint -q "$WS" || mount "$WS"
chown ruu-hands:ruu-hands "$WS"
chmod 700 "$WS"

install -o root -g root -m 755 "$SRC/runner.py" /opt/ruu-hands/runner.py
install -o root -g root -m 644 "$SRC/ruu-hands.socket" /etc/systemd/system/ruu-hands.socket
install -o root -g root -m 644 "$SRC/ruu-hands@.service" "/etc/systemd/system/ruu-hands@.service"
systemd-analyze verify "/etc/systemd/system/ruu-hands@.service" 2>&1 | grep -v "^$" | head -5 || true
systemctl daemon-reload
systemctl enable --now ruu-hands.socket
systemctl is-active ruu-hands.socket
df -h "$WS" | sed 1d
