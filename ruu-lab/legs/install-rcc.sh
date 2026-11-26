#!/bin/sh
# Install the pinned RCC bundle on the controller. A single verified CLI file is also accepted.
set -eu
revision=577a846642c43e7332df4980291890bec68b89ef
bundle=3b498acca4ffe719fc477b09862dea185c3f3ce474a9c274d0cf4826adc9b229
installer=288d268c537c6570cd12ef36a69ca42dfb3b16b2d7e7400c772f15ee26c87ac9
client=a380858dca3826beafbd189c16b4d626780ea05a06a98e368f2f79ae58cef224
target=${RCC_INSTALL_PATH:-/usr/local/bin/rcc}
if [ "$#" -gt 1 ]; then
    echo "usage: $0 [RCC-checkout-or-client-file]" >&2
    exit 2
fi
check_file() {
    actual=$(sha256sum "$2")
    if [ "${actual%% *}" != "$1" ]; then
        echo "RCC checksum mismatch for $revision" >&2
        exit 1
    fi
}
if [ "$#" -eq 1 ] && [ -f "$1" ]; then
    dir=$(dirname "$target")
    mkdir -p "$dir"
    staged=$(mktemp "$dir/.rcc.XXXXXXXX")
    trap 'rm -f "$staged"' EXIT
    cat "$1" > "$staged"
    check_file "$client" "$staged"
    chmod 0755 "$staged"
    mv -f "$staged" "$target"
    exit 0
fi

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
if [ "$#" -eq 1 ]; then
    cp -Rp "$1/rcc" "$1/rccd" "$1/install.py" "$1/plugins" "$stage/"
else
    git init -q "$stage/repo"
    git -C "$stage/repo" fetch --quiet --depth 1 https://github.com/syn-acc/rcc.git "$revision"
    test "$(git -C "$stage/repo" rev-parse FETCH_HEAD)" = "$revision"
    git -C "$stage/repo" archive --output="$stage/payload.tar" "$revision" -- rcc rccd install.py plugins
    tar -xf "$stage/payload.tar" -C "$stage"
fi
check_file "$installer" "$stage/install.py"
python3 -IB "$stage/install.py" --expect-digest "$bundle" --target "$target"
