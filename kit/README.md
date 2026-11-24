# The kit: one machine, one seed per agent

How Ruu was made, consolidated so the next one does not start from a blank file.

**State, 2026-11-21.** Built and tested (69 checks, on this PC and on uk). Not deployed: Ruu on uk
still runs `ruu-lab/soft_machine.py`. No second agent exists. Nothing here has met a real model,
Discord or the sandbox; those are stand-ins in the tests.

## The split

Ruu was one 1,600-line runner with the character written into it. The kit separates three things.

| Thing | Where | Holds |
| --- | --- | --- |
| The machine | `machine/` | The loop, orders, hands, budget, dice mechanics, library builder, scanner, and the texts tied to the code. The same for every agent. |
| A seed | `seeds/<id>/` | Who one agent is and what it wants. No code. |
| A config | `seeds/<id>/config.json` | Where it lives and what it may spend: channel, caps, models, weekly ceiling. |

`seeds/ruu/` is Ruu, generated from the deployed files. `seeds/_template/` is a blank one.

## What an agent is made of

Each part exists because of something that went wrong without it. That is the method: a failure
becomes a mechanism on the host, not another line in the prompt.

| Part | File | Why it exists |
| --- | --- | --- |
| Character | `persona.md` | Who it is. Given on every turn that speaks. |
| Convictions | `convictions.md` | What it believes, as background it never recites. Stated as a list, it lectures. |
| Register | `register.md` | The hard rules of its voice. Ruu's first day was props, glyph sign-offs and narrating its own workshop. |
| Dice | `seed.json` → `dice` | Register, temper and volume, rolled by the host and put at the top of the prompt. Without them every post sounds the same. |
| Forms | `seed.json` → `forms`, `census` | What it makes when nobody asked, and how often each kind. |
| Presence | machine | Its own channel; react, reply, tag, speak or let it pass; typing while it works; people weighted by how much they have talked to it, the owner at 1.0. |
| Library | `library.json`, `library_src/` | **It only gets to know what it may say.** Ruu recited its own design pack; a prompt rule did not stop it. |
| Orders and drives | machine; `seed.json` → `drives` | Finishing a step schedules the next, and a standing motive returns whenever nothing is open. Otherwise it waits to be prodded. |
| Hands | machine, `machine/units/hands__*` | A sandbox with no network. The host posts what a run really printed, so the agent cannot claim work it did not do. |
| Bench | `machine/benches/`, `seed.json` → `bench` | A score computed where the agent cannot reach. Ruu "improved" a model by multiplying its last score by 0.64. Then it spent a night on a one-example gain, so the host also tells it the noise floor and what plain methods score. |
| Look outside | `scan.json` | One digest a day, stripped and labelled as other people's claims. |
| Budget | `config.json` | A weekly ceiling on the shared login, paced as tokens per hour; cheaper models for cheaper turns. |
| Confidentiality | machine (`protocol.py`) | What is never disclosed, and stripping of links and mentions. A seed cannot remove it. |

## Making another one

What only the person asking can supply:

1. **A subject and a world**: what it is about, and the place it speaks from.
2. **A motive**: what it keeps building when nobody asks, and what one finished piece looks like.
3. **A number**: something that would show it working, computed by the host. If there is none, the
   agent can show work and cannot prove improvement, and the check says so.
4. **An owner**: one person who gives it orders and reads its channel in the first days.
5. **A home and a bill**: a channel, a bot account of its own, and a share of a model budget.

Then:

    python machine/seed.py new seeds/vex --id vex --name Vex --owner Sam --world "the Foundry" \
        --place "the Foundry server" --channel "#vex-building"
    # answer every FILL: in seeds/vex/, copy config.example.json to config.json and fill it in
    python machine/seed.py check seeds/vex --config seeds/vex/config.json --sharing seeds/ruu/config.json
    python machine/seed.py render seeds/vex out/          # its service files; installs nothing

On the host, in this order, stopping at the first thing that looks wrong:

1. `build_library.py --seed DIR`, then read `library/`: that is everything it will know.
2. Dry turns, nothing posted: `soft_machine.py --seed DIR --config FILE --dry-own creation --roll plain/*/short`
   and `--dry-respond "hello"`. Read twenty. Whatever reads as costume gets named and banned in `register.md`.
3. Hands: `out/hands/setup.sh` as root. A bench, if it has one, before any build order.
4. Start it with `"enabled": true`. It says hello once. Give the first order from above: get to grips
   with its own library.
5. Read the journal and the channel for a day. A fabricated result, a recited paper or a leaked path
   means a missing mechanism; fix it there.

## What one more agent costs

| Cost | Detail |
| --- | --- |
| Model budget | Every agent on uk draws on one Codex login. Ruu's ceiling is 60% of the week. `seed.py check --sharing` refuses ceilings that pass 100%. |
| A bot account | One Discord application and token per character, created by its owner. `token_env` in the config names it. |
| A sandbox | One system user, a 1 GB disk image and a socket per agent. |
| An owner | Someone reading the channel and giving orders. Ruu's first two days took repeated corrections, each of which became one of the parts above. |
| The host | One daemon, capped at 768 MB. |

## Not consolidated

- **The model transport.** The machine calls models through the Codex client in the Hermes runtime
  on uk. An agent hosted elsewhere, or one running on an open model on the 3090, needs a second
  transport. Not built.
- **Benches.** Only OpenRSI's exists (`benches/openrsi.py`, installer beside it). Every new motive
  needs its own: a task in the sandbox, answers outside it, a score function, reference methods.
- **The child system** (`ruu-lab/child/`) is Ruu's model-development rig and is not part of this.
- **The idle poster** in the other channel (`discord-idle`) is a separate plugin. Ruu's persona paper
  is shared with it; on uk the seed should link to that file, not copy it.
- **The journal** still uses Ruu's glyphs as status codes for every agent.
- **Hands are assumed.** A seed with drives and no sandbox installed will fail its build orders.

## Moving Ruu onto the kit (not done; needs a yes)

1. Copy `machine/` to the host beside the plugins.
2. Add to `plugins/ruu-lab/`: `seed.json`, `register.md`, `library.json`, `scan.json`; `convictions.md`
   (today's `dna.md`); `persona.md` as a link to the idle plugin's.
3. Install the two rendered units whose `ExecStart` changed; restart; check with `--meter` and one dry turn.
4. Back out by restoring the two unit files. The state database is untouched either way.

`drives` in the live config is ignored after the move: drives live in the seed.

## What the tests show

    python tests/test_kit.py          # 69 checks, about 40 seconds, no network

- With Ruu's seed, the kit and the deployed runner were lived through the same scripted 36 hours
  (same dice, clock, channel, model answers and sandbox results): 195 model turns, 8,822 Discord
  calls, 67 sandbox jobs, 23 scored runs. Every instruction text, prompt, call, job, journal line
  and database row matched.
- The library builder and the scanner each produce the same output as the deployed ones from the
  same inputs. Rendered service files match the installed ones, apart from where the kit is
  installed and the line endings of `runner.py` (the deployed copy has Windows ones; harmless as it
  is started, worth replacing at the next install).
- A second agent scaffolded from the blank seed is refused until its questions are answered, then
  lives 22 scripted hours in which nothing it is told or says carries Ruu's name, world, owner or bench.

They do not show that a real model behaves the same (it is given the same words, which is the most a
test without a model can show), or that a new character is any good. Only dry turns show that.

## Files

    machine/soft_machine.py      the runner
    machine/protocol.py          the words every agent is given
    machine/seed.py              load, check, new, render
    machine/build_library.py     what it may read
    machine/frontier_scan.py     the look outside
    machine/benches/             openrsi.py (scoring), openrsi_install.py (the task)
    machine/units/               service file templates
    seeds/ruu/                   Ruu
    seeds/_template/             a blank agent
    tests/test_kit.py
