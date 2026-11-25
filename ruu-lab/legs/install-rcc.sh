#!/bin/sh
# Install the RCC client version used by this RUUF revision. An optional file allows offline installs.
set -eu
revision=28759b7cb0b081565050af7582fac75abcdafd20
digest=6242086b81ede8ab162cc39a7249c96926dbc739b48f0a782b42c7191c979160
target=${RCC_INSTALL_PATH:-/usr/local/bin/rcc}
if [ "$#" -gt 1 ]; then
    echo "usage: $0 [path-to-rcc]" >&2
    exit 2
fi
dir=$(dirname "$target")
mkdir -p "$dir"
staged=$(mktemp "$dir/.rcc.XXXXXXXX")
trap 'rm -f "$staged"' EXIT
if [ "$#" -eq 1 ]; then
    cat "$1" > "$staged"
else
    curl --fail --silent --show-error --location \
        "https://raw.githubusercontent.com/syn-acc/rcc/$revision/rcc" > "$staged"
fi
actual=$(sha256sum "$staged")
if [ "${actual%% *}" != "$digest" ]; then
    echo "RCC checksum mismatch for $revision" >&2
    exit 1
fi
chmod 0755 "$staged"
mv -f "$staged" "$target"
