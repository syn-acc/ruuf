# The launcher

ruuf is a series of processes. `ruufd` is the one that knows what they are and where everything
lives. It reads one file, `ruuf.toml` at the repository root, and builds two things from it.

| Thing | What it is |
| --- | --- |
| The map | Names to places. A part asks for `state` or `decisions` by name and never builds a path. |
| The process table | One entry per part: its command, its user, how it starts, what it may read and write, its limits. |

## State

This is the skeleton. It checks the manifest and answers questions about it. **It starts nothing.**
Every part is still run by the systemd unit named in its entry, and no code reads the map yet: the
soft machine, idle poster and scanner still carry their own paths. `ruuf.toml` describes the
deployment as it runs; a test holds each entry's command and user to the unit file it stands for.

## Use

    python launcher/ruufd.py check            # is the manifest sound
    python launcher/ruufd.py check --host     # and does this machine match it
    python launcher/ruufd.py where            # the whole map
    python launcher/ruufd.py where state      # one place
    python launcher/ruufd.py status           # the process table
    python launcher/ruufd.py map              # the map as the agent may be shown it

    python -m unittest discover launcher/tests

Python 3.11 or later, standard library only. (On 3.10, install `tomli`.)

To point the same manifest at another machine or a scratch folder, override a var:

    RUUF_VAR_HOME=/tmp/scratch python launcher/ruufd.py where

## What the manifest refuses

`check` reports every problem at once. Beyond missing fields and names that do not resolve, three
rules protect the sandboxes, the parts that run code the agent wrote:

1. A sandbox has `limits.network = false`.
2. A sandbox does not read or write a `secret` place.
3. A sandbox does not share a user with any part that reads a `secret` place.

`map` leaves secret places out and gives no paths: the agent learns what exists and what it holds.

## What comes next

In order: start, watch, restart and log one part; timers; hand each part its places so the paths
come out of the code; then the sockets and the sandboxes, which keep every limit their units set
today. The launcher is meant to run as root, because it starts parts as different users. Nothing
here does that yet.
