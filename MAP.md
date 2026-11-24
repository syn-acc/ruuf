# The map

Where things are in this repository today, one line each. If you are an agent, read `AGENTS.md` first.
For where things are on a running machine, ask the launcher: `python launcher/ruufd.py where`.

| Path | What lives there |
| --- | --- |
| `AGENTS.md` | Start here if you are an agent. |
| `README.md` | What ruuf is, the layout, and what is deliberately not in the repository. |
| `ruuf.toml` | The manifest: every place and every part, by name. The launcher reads this and nothing else. |
| `launcher/` | `ruufd`: knows what the parts are and where everything lives; starts and watches them. `origin.py` is what it demands from the operator before it starts anything. |
| `routines/` | Rules written as programs. `ship.py` is how a change reaches main. |
| `plugins/discord/` | The one Discord plugin, with a folder per interface it supplies (ears, voice, eyes, rooms). Only its client is here so far; the three older `discord-*` folders move in step by step. |
| `ruu-lab/` | The soft machine (the loop that speaks and works; it has eyes: it is shown the pictures its last run drew), the daily frontier scan, the convictions (`dna.md`), the library's sources, and the sandboxes it uses: `hands/` (runs the agent's own code, no network), `legs/` (work on rented machines elsewhere; Colab is the first kind) and `child/` (work on rented machines, with `child/scorebox/`, the judge it cannot reach). |
| `plugins/discord-idle/` | The idle poster: one line after a long silence. Holds the persona. |
| `plugins/discord-niches/` | The reaction-role menu for the server's niche groups. |
| `plugins/discord-reader/` | Reads Discord history on request: the ears. It can also react, reply and pin when asked to. |
| `hermes/` | The patch to the Hermes agent this deployment rides on, and files added to it: colour roles, niche roles, the meeting listener. `apply.sh` rebuilds the runtime. |
| `deploy/units/` | The systemd units that run each part today. |
| `kit/` | An older packaged copy of the lab. Behind the live code; do not treat it as the source. |
| `.gitignore`, `.gitattributes` | What is kept out of the repository (tokens, live config, state), and Unix line endings for every text file. |

## What belongs to whom

The framework (launcher, routines, the sandboxes, the loop) and the one agent that lives here (its
persona, convictions, moods, library, channels) are still mixed together in these folders. They are
being separated: the framework at the top, the agent under one folder of its own. Until then, treat
anything that names a persona, a channel or a mood as the agent's, and everything else as the framework's.
