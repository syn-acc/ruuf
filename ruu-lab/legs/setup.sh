#!/bin/sh
# Install ruu legs on the server. Run as root from the folder holding these files.
set -eu
install -d -m 755 -o root -g root /opt/ruu-legs
install -m 644 -o root -g root runner.py rules.json /opt/ruu-legs/
install -d -m 700 -o root -g root /data/ruu-legs /data/ruu-legs/logs
install -m 644 -o root -g root ruu-legs.socket 'ruu-legs@.service' /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ruu-legs.socket
systemctl is-active ruu-legs.socket
