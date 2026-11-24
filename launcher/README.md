# The launcher

ruuf is a series of processes. `ruufd` is the one that knows what they are and where everything
lives. It reads one file, `ruuf.toml` at the repository root, and builds two things from it.

| Thing | What it is |
| --- | --- |
| The map | Names to places. A part asks for `state` or `decisions` by name and never builds a path. |
| The process table | One entry per part: its command, its user, how it starts, what it may read and write, its limits. |

## State

The launcher checks the manifest, answers questions about it, and can start and watch parts:
always-on parts and timers, with restarts, time limits and one log file per part.

**On the deployment it starts nothing.** Every part in `ruuf.toml` still names the systemd unit
that runs it, and the launcher refuses to start a part that names one, so nothing can be started
twice. A part is handed over by stopping its unit and removing `unit` from its entry. Socket parts
(the sandboxes) are refused for now: the launcher cannot yet apply their limits.

The soft machine, idle poster and scanner take their places from the launcher when it starts them
(`RUUF_PLACE_<NAME>`), and fall back to the deployment's paths when a unit starts them, as now. A
test holds each part's entry to the places its code asks for.

## The origin

A persona and a standing motive were written from something: a person, a place, what the two are
to each other, and why the person wanted an agent there. That is the origin. ruuf holds none of its
own. The launcher demands it from the operator and starts nothing until every question is answered.

    python launcher/ruufd.py origin         # what is known, what is guessed, what is still demanded
    python launcher/ruufd.py origin ask     # the operator answers, at a terminal
    python launcher/ruufd.py origin blank   # the file with nothing answered, to fill in by hand

| | |
| --- | --- |
| What is asked | Who the operator is (`operator`), what they are to the agent and what their word is to it (`relation`), where it lives (`world`), and why it exists and what it does when left alone (`spark`). `origin.py` holds the questions. |
| What the launcher works out | Who is asking, from the machine: the login, who is behind `sudo`, the clone's git identity, whether the manifest is theirs, whether the login is one a part runs as. From that it says what the caller looks like: the one who runs the machine, the one who set it up, the agent's own account, or a visitor. |
| Guesses | A name and a relation may be guessed. A guess is shown as a guess and becomes an answer only when the operator presses Enter on it in `origin ask`. |
| Who may answer | The operator, at a terminal. With no terminal `origin ask` refuses: an agent does not answer for its operator. |
| Where it is kept | The place called `origin`, on the host, readable by its owner only. Never in the repository. |
| What is shown | Which questions are answered, by whom and when. The answers themselves are never printed. |
| What it gates | `run` refuses while a question is open. `check` reports the state and does not fail on it. |

Nothing reads the origin yet beyond the launcher. Deriving the persona, the convictions and the
drives from it is the next step, and so is working out who a person is to the agent while it is
talking to them, which today is one number per member in the soft machine.

## Use

    python launcher/ruufd.py check            # is the manifest sound
    python launcher/ruufd.py check --host     # and does this machine match it
    python launcher/ruufd.py where            # the whole map
    python launcher/ruufd.py where state      # one place
    python launcher/ruufd.py status           # the process table, live if the launcher is running
    python launcher/ruufd.py map              # the map as the agent may be shown it
    python launcher/ruufd.py run --all        # start every part the launcher owns; stays in the foreground
    python launcher/ruufd.py run --only a b   # or only these
    python launcher/ruufd.py stop             # stop the launcher and everything it started

    python -m unittest discover launcher/tests

Python 3.11 or later, standard library only. (On 3.10, install `tomli`.)

To point the same manifest at another machine or a scratch folder, override a var:

    RUUF_VAR_HOME=/tmp/scratch python launcher/ruufd.py where

## How a part is run

| | |
| --- | --- |
| User | As the user its entry names. Only root can start a part as someone else; `--as-self` runs everything as the current user, for scratch folders. |
| Places | It is told where its places are through `RUUF_PLACE_<NAME>`, and only the ones it lists under `reads` and `writes`. Also `RUUF_PART` and `RUUF_MANIFEST`. |
| Output | Appended to `<logs>/<part>.log`, with the launcher's own lines marked `[ruufd ...]`. |
| Restart | `no`, `on-failure` or `always`, after `restart_seconds`. Five starts inside a minute and the part is left down. |
| Timers | `every_seconds` counts from when the last run ended. `daily_at` is HH:MM UTC. |
| Time limit | `limits.runtime_seconds`: past it the part is killed. The other limits are recorded and not yet applied. |
| Stopping | The part and everything it spawned get SIGTERM, then SIGKILL after ten seconds. |

The launcher writes what it is running to `<launcher>/ruufd.json`; `status` and `stop` read it.

## What the manifest refuses

`check` reports every problem at once. Beyond missing fields and names that do not resolve, three
rules protect the sandboxes, the parts that run code the agent wrote:

1. A sandbox has `limits.network = false`.
2. A sandbox does not read or write a `secret` place.
3. A sandbox does not share a user with any part that reads a `secret` place.

`map` leaves secret places out and gives no paths: the agent learns what exists and what it holds.

## What comes next

In order: the sockets and the
sandboxes, which keep every limit their units set today; memory and task limits for ordinary
parts; then handing the first real part over from its unit. The launcher is meant to run as root, because it starts parts as different users.
