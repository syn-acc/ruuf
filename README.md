# ruuf

Ruu's framework. Ruu is the resident agent of SCRU (Synthetic Culture Research Unit) on
Discord. This repository holds everything custom that makes Ruu what it is, on top of an
unmodified upstream agent runtime.

## Layout

| Path | What it is |
| --- | --- |
| `hermes/` | Patches and new files on top of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) at the commit in `UPSTREAM_COMMIT`: Discord adapter and gateway changes, colour roles, niche roles, meeting listener, their tests. `apply.sh` rebuilds the runtime. |
| `plugins/discord-idle/` | The idle poster: one in-character line after a long human silence. Holds `persona.md`. |
| `plugins/discord-niches/` | Reaction-role menu for the server's niche groups. |
| `plugins/discord-reader/` | MCP server that lets the agent read Discord channels, scoped to the requester. |
| `ruu-lab/` | The soft machine: Ruu's continuous daemon for its own channel, with standing orders, a library, a usage budget and model routes. |
| `ruu-lab/hands/` | The sandbox Ruu runs code in: no network, one job per connection. |
| `ruu-lab/child/` | The child system: Ruu improves an open model and the host scores it. |
| `kit/` | The same machine with the character taken out, plus a seed per agent, for making another agent like Ruu. |
| `deploy/units/` | The systemd units as deployed. |

## What is not here

Tokens, `.env` files, live config, state databases, logs, sessions, member data and the
generated library are kept on the host and are ignored by `.gitignore`. Config files in
this repository are examples: fill in your own guild, channel, bot and owner ids.

## State

This is a deposit of running code, not a packaged release. The plugins and `ruu-lab` match
what is deployed. `kit/` was generated from an earlier `ruu-lab` and is one change behind
it (`kit/tests/test_kit.py` reports the difference in `hands/runner.py`).
