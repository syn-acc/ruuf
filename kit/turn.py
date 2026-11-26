#!/usr/bin/env python3
"""Run one adapter call using the caller's instructions and JSON input."""

import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time


INPUT_LIMIT = 65_536
TEXT_LIMIT = 262_144
OUTPUT_LIMIT = 1_048_576


def read_bytes(stream, limit):
    raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"input exceeds {limit} bytes")
    return raw


def decode_json(raw):
    def invalid_number(value):
        raise ValueError(f"invalid JSON number: {value}")
    return json.loads(raw.decode("utf-8"), parse_constant=invalid_number)


def read_file(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("expected a regular file")
        return read_bytes(stream, limit)


def bundle_file(root, name, limit):
    """Read a relative regular file without following symlinks out of the bundle."""
    if not isinstance(name, str) or not name or any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError("bundle files must be relative paths inside the seed")
    parts = name.split("/")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("expected a regular bundle file")
            return read_bytes(stream, limit)
    finally:
        os.close(directory)


def instructions(root):
    spec = decode_json(bundle_file(root, "seed.json", INPUT_LIMIT))
    if not isinstance(spec, dict) or set(spec) - {"instructions", "instruction_file", "files"}:
        raise ValueError("seed.json may contain instructions or instruction_file, and optional files")
    if ("instructions" in spec) == ("instruction_file" in spec):
        raise ValueError("provide exactly one of instructions and instruction_file")
    if "instruction_file" in spec:
        raw = bundle_file(root, spec["instruction_file"], TEXT_LIMIT)
    else:
        if not isinstance(spec["instructions"], str):
            raise ValueError("instructions must be text")
        raw = spec["instructions"].encode("utf-8")
    return raw.decode("utf-8"), spec.get("files", {})


def named_files(root, names):
    if not isinstance(names, dict) or len(names) > 32:
        raise ValueError("files must map up to 32 caller-chosen names to paths")
    remaining = TEXT_LIMIT
    result = {}
    for name, path in names.items():
        raw = bundle_file(root, path, remaining)
        result[name] = raw.decode("utf-8")
        remaining -= len(raw)
    return result


def invoke(config, request):
    if not isinstance(config, dict):
        raise ValueError("runtime config must be a JSON object")
    argv = config.get("model_command")
    if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)
            and os.path.isabs(argv[0])):
        raise ValueError("model_command must be an argv array starting with an absolute executable")
    timeout = config.get("timeout_seconds", 120)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("timeout_seconds must be an integer from 1 to 3600")
    names = config.get("pass_env", [])
    if not isinstance(names, list) or not all(isinstance(n, str) and n.isidentifier() for n in names):
        raise ValueError("pass_env must list environment variable names")
    environment = {"PATH": os.defpath}
    environment.update({name: os.environ[name] for name in names if name in os.environ})
    with tempfile.TemporaryDirectory() as work, tempfile.TemporaryFile() as stdin:
        stdin.write(json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        stdin.seek(0)
        with subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              env=environment, cwd=work, start_new_session=True) as proc:
            try:
                deadline = time.monotonic() + timeout
                output = bytearray()
                with selectors.DefaultSelector() as ready:
                    ready.register(proc.stdout, selectors.EVENT_READ)
                    while True:
                        if not ready.select(max(0, deadline - time.monotonic())) or time.monotonic() > deadline:
                            raise ValueError("model adapter timed out")
                        chunk = os.read(proc.stdout.fileno(), min(65536, OUTPUT_LIMIT + 1 - len(output)))
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > OUTPUT_LIMIT:
                            raise ValueError("model adapter output exceeds 1 MiB")
                code = proc.wait(timeout=max(0, deadline - time.monotonic()))
                if code:
                    raise ValueError(f"model adapter exited with status {code}")
            except (ValueError, OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
                # On an interrupted or oversized response, cancel this invocation's process group.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise
    decode_json(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=os.environ.get("RUUF_PLACE_SEED"))
    parser.add_argument("--config", type=Path, default=os.environ.get("RUUF_PLACE_CONFIG"))
    parser.add_argument("--input", default="-", help="one JSON value from a file or stdin (default)")
    parser.add_argument("--show-prompt", action="store_true", help="write the exact instructions and exit")
    args = parser.parse_args()
    if args.seed is None:
        parser.error("no seed: pass --seed DIR or set RUUF_PLACE_SEED")
    try:
        text, names = instructions(args.seed)
        if args.show_prompt:
            sys.stdout.buffer.write(text.encode("utf-8"))
            return
        if args.config is None:
            parser.error("no adapter config: pass --config FILE or set RUUF_PLACE_CONFIG")
        config = decode_json(read_file(args.config, INPUT_LIMIT))
        raw = read_bytes(sys.stdin.buffer, INPUT_LIMIT) if args.input == "-" else read_file(args.input, INPUT_LIMIT)
        request = {"instructions": text, "input": decode_json(raw)}
        files = named_files(args.seed, names)
        if files:
            request["files"] = files
        sys.stdout.buffer.write(invoke(config, request))
    except (OSError, ValueError, RecursionError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"turn: {exc}\n")


if __name__ == "__main__":
    main()
