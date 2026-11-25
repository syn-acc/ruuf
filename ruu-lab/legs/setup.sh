#!/bin/sh
# Install ruu legs on the server. Run as root from the folder holding these files.
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
"$here/install-rcc.sh" "$@"
install -d -m 755 -o root -g root /opt/ruu-legs
install -m 644 -o root -g root "$here/runner.py" "$here/rules.json" /opt/ruu-legs/
install -d -m 700 -o root -g root /data/ruu-legs /data/ruu-legs/logs
units="$here"
if [ ! -f "$units/ruu-legs.socket" ]; then
    units="$here/../../deploy/units"
fi
install -m 644 -o root -g root "$units/ruu-legs.socket" "$units/ruu-legs@.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ruu-legs.socket
systemctl is-active ruu-legs.socket
