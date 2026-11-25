#!/bin/sh
# Install the pinned RCC bundle on the controller. A single verified CLI file is also accepted.
set -eu
revision=538d8f548a1590c80dc2ca5a0ddad476bf6b07f7
bundle=3352a7fc7657a67f7097dd1b9c011198907f8f8968e3f2d15758b676d57b1c83
installer=b57ef9c456464c8fe30fcf71c1f579d54d2ef18b873b99db33dae93a9530119b
client=d9eeb843794a1a6c424404e61ff210d253ef3f2930955da5cc96e4e9e606f62d
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
    cp -Rp "$1/rcc" "$1/install.py" "$1/plugins" "$stage/"
else
    git init -q "$stage/repo"
    git -C "$stage/repo" fetch --quiet --depth 1 https://github.com/syn-acc/rcc.git "$revision"
    test "$(git -C "$stage/repo" rev-parse FETCH_HEAD)" = "$revision"
    git -C "$stage/repo" archive --output="$stage/payload.tar" "$revision" -- rcc install.py plugins
    tar -xf "$stage/payload.tar" -C "$stage"
fi
check_file "$installer" "$stage/install.py"
python3 -IB "$stage/install.py" --expect-digest "$bundle" --target "$target"
