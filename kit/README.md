# Run one turn

`turn.py` gives an operator-selected model adapter the caller’s instructions and one JSON input. The caller chooses the meaning of the input and output. One invocation creates no persistent session. Instance packages supply any persona, memory, channel policy, order loop, social runtime or evaluation they need.

## Seed

Create `seed.json` in a directory you control. It accepts either a literal `instructions` string or an `instruction_file` path. The optional `files` object maps caller-chosen labels to UTF-8 files inside that directory.

```json
{
  "instruction_file": "task.txt",
  "files": {"units": "notes/units.txt"}
}
```

To inspect the exact instructions, including whitespace, run the following from the framework checkout. The preview requires only the seed.

```sh
python3 kit/turn.py --seed /path/to/seed --show-prompt
```

## Adapter

The operator provides a separate JSON config. `model_command` is the executable and arguments used for each invocation. Its first element must be an absolute executable path. The following is an illustrative configuration.

```json
{
  "model_command": ["/usr/local/bin/my-model-adapter"],
  "timeout_seconds": 120
}
```

```sh
python3 kit/turn.py --seed /path/to/seed --config /path/to/config.json --input /path/to/input.json
```

Input defaults to stdin. The runner also accepts the explicitly configured paths `RUUF_PLACE_SEED` and `RUUF_PLACE_CONFIG`. The adapter receives one UTF-8 JSON object on stdin with `instructions`, `input`, and `files` when files were supplied. For example, an adapter might receive the following.

```json
{"instructions":"Sort the samples.","input":[13,-1,7],"files":{"units":"millimetres"}}
```

The adapter writes a single JSON value on stdout. The runner validates that value and forwards its bytes unchanged. A nonzero adapter exit fails the turn. The subprocess starts in a temporary directory with a standard executable search path. Use the optional config array `pass_env` to forward named environment variables to a trusted adapter.

The input and each configuration document are limited to 64 KiB. Instruction files and the combined named files each have a 256 KiB limit. The runner accepts up to 32 named files and a 1 MiB response. Bundle paths are relative regular files. Symlinks and parent-directory traversal are refused.

Run `python3 -IB -m unittest discover -s kit/tests` for an offline example. It verifies the complete adapter request and response using a numeric sorting task.
