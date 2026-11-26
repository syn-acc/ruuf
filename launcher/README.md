# The RUUF launcher

`ruufd` reads an instance manifest. The operator specifies its processes, files, model providers, and permissions. An instance can run a local program with zero providers. The framework ships a passive example that you can validate from the checkout.

```sh
python3 launcher/ruufd.py --manifest ruuf.toml check
```

## Create an instance

Choose a directory you own outside the framework checkout and an identifier for your resident.

```sh
python3 launcher/ruufd.py init "$HOME/my-instance" --id my-instance
export RUUF_MANIFEST="$HOME/my-instance/ruuf.toml"
python3 launcher/ruufd.py check
python3 launcher/ruufd.py origin ask
```

`init` creates an empty process table and instance-relative paths for logs and supervisor state. It refuses to overwrite an existing manifest. Run each command with `RUUF_MANIFEST` set, or supply `--manifest FILE` before the command. The flag takes precedence.

At a terminal, the operator supplies a name and the intended purpose of the instance. The default answer keys are `operator.name` and `instance.purpose`. Answers live in the instance's owner-only `origin.toml`, or the file chosen by `places.origin`. The launcher checks that the required fields have answers before starting a part. `origin` reports completion and `origin blank` prints the instance's unanswered form.

An instance can replace the default form by listing its questions in the manifest. Each entry has a unique `section.field` key, a prompt, an optional `why` explanation, and a `required` flag that defaults to true. At least one question must be required. Keeping a field's key preserves its existing answer when the wording changes. Answers to retired fields stay in the private origin file.

```toml
[[origin.questions]]
key = "operator.name"
ask = "Which name should this instance record for its operator?"
required = true

[[origin.questions]]
key = "instance.purpose"
ask = "What do you intend to use this instance for?"
required = true
```

## Configure a part

Add an entry to the instance manifest for each installed program. For example, this timer runs an operator-supplied Python script once per hour. Replace `your-account` with a local user. Put the script and its input in the instance directory.

```toml
[places.input]
path = "observations.txt"
kind = "file"
holds = "Observations for the local report."

[parts.report]
run = ["python3", "{root}/report.py"]
cwd = "{root}"
user = "your-account"
start = "timer"
every_seconds = 3600
reads = ["input"]
```

The process receives the path as `RUUF_PLACE_INPUT`. Parts receive the places they declare under `reads` and `writes`, plus `RUUF_PART` and `RUUF_MANIFEST`. These declarations describe the launcher interface. Operating-system accounts and sandboxing determine filesystem access.

Relative place paths and working directories resolve from the manifest directory. `{root}` names that directory and `{framework}` names the RUUF installation. Declare additional roots in `[vars]` and override them with `RUUF_VAR_<NAME>`. A model ladder, when supplied, names registered providers in order of preference.

```sh
python3 launcher/ruufd.py check --host
python3 launcher/ruufd.py where input
python3 launcher/ruufd.py map
python3 launcher/ruufd.py run --only report
# From another terminal using the same manifest
python3 launcher/ruufd.py status --json
python3 launcher/ruufd.py stop
```

## Supervision

`run --all` starts the instance's eligible parts in the foreground. Always-on processes and timers support restart policies, delays and runtime limits. The supervisor records per-part output under `places.logs` and the running process table under `places.launcher`. Give each instance its own directories. Stopping sends SIGTERM to each process group, then SIGKILL after ten seconds if needed.

A part with a `unit` belongs to an external service manager. Manual and socket-activated parts also require their declared manager. The launcher checks sandbox network and secret-access declarations, and leaves sandbox execution to a manager that can enforce its limits. Parts run under their named account. Switching accounts requires the corresponding OS privileges. Use `--as-self` for local fixtures.

Run the launcher tests with `python3 -m unittest discover launcher/tests`. Python 3.11 and later use the standard library. Python 3.10 requires `tomli`.
