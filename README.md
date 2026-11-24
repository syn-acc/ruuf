# ruuf

A framework for one resident agent. Ruu, the resident agent of SCRU (Synthetic Culture Research
Unit) on Discord, is the first agent it carries, and most of what is here is still Ruu's.

ruuf itself insists on two things and supplies neither: an **origin** (who the operator is, what
they and the agent are to each other, and why the agent exists, in the operator's own words) and a
**ladder** of models, best first. A persona, convictions and a standing motive are made from the
origin; they are not where an agent starts. The launcher demands the origin and starts nothing
without it: `python launcher/ruufd.py origin`.

Being pulled apart, in this order: the launcher asks for the origin (done); the persona,
convictions and drives are derived from it instead of written by hand; Ruu's own files move out
from under the framework's.

## Layout

| Path | What it is |
| --- | --- |
| `hermes/` | Patches and new files on top of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) at the commit in `UPSTREAM_COMMIT`: Discord adapter and gateway changes, colour roles, niche roles, meeting listener, their tests. `apply.sh` rebuilds the runtime. |
| `plugins/discord-idle/` | The idle poster: one in-character line after a long human silence. Holds `persona.md`. |
| `plugins/discord-niches/` | Reaction-role menu for the server's niche groups. |
| `plugins/discord-reader/` | MCP server that lets the agent read Discord channels, scoped to the requester. |
| `ruu-lab/` | The soft machine: Ruu's continuous daemon for its own channel, with standing orders, a library, a usage budget and model routes. |
| `ruu-lab/hands/` | The sandbox Ruu runs code in: no network, one job per connection. |
| `ruu-lab/legs/` | Work on rented machines elsewhere. Ruu sends the parameters of an experiment, never code; the host starts it, meters it and scores it. |
| `ruu-lab/child/` | The child system: Ruu improves an open model and the host scores it. |
| `kit/` | The same machine with the character taken out, plus a seed per agent, for making another agent like Ruu. |
| `deploy/units/` | The systemd units as deployed. |
| `ruuf.toml` | The manifest: the model ladder, the map of where everything lives, and the process table. |
| `launcher/` | `ruufd`, the launcher that reads the manifest. It checks the manifest, answers questions about it, demands the origin, and can start and watch parts. On the deployment it starts nothing yet: every part still belongs to its unit. |
| `routines/` | Rules written as programs. `ship.py` is how a change reaches main. |

## What is not here

Tokens, `.env` files, live config, state databases, logs, sessions, member data and the
generated library are kept on the host and are ignored by `.gitignore`. Config files in
this repository are examples: fill in your own guild, channel, bot and owner ids.

## State

This is a deposit of running code, not a packaged release. The plugins and `ruu-lab` match
what is deployed. `kit/` was generated from an earlier `ruu-lab` and is one change behind
it (`kit/tests/test_kit.py` reports the difference in `hands/runner.py`).
