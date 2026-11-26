# ⟡ the soft machine

Ruu's continuous runner for `#ruu-building`. One file, one sqlite database, one
systemd service (`ruu-soft-machine.service`). Esoteric on the surface, plain
underneath: every glyph maps to an ordinary state.

| Glyph | Journal status | Meaning |
| --- | --- | --- |
| ⟡ | `⟡ said` / `⟡ react` / `⟡ reply` / `⟡ reply_ping` / `⟡ tag` / `⟡ say` | Ruu acted, in that mode |
| ∿ | `∿ let it pass` | Ruu or the dice chose not to answer |
| ⊘ | `⊘ skipped by ruu` / `⊘ metabolism` / `⊘ error` / `⊘ disabled` | The model chose silence / shared login past its ceiling / failure / config off |
| ꙮ | `ingest()` | A member message was heard and counted |

The journal never contains post text. `journalctl -u ruu-soft-machine -o cat`.

## The loop

Every `poll_seconds` the daemon reads new messages in the lab channel.

- **Someone spoke.** It waits until they have been quiet for `debounce_seconds`
  (or 25 s at most) so it does not cut in, then takes everything new as one batch.
  A dice gate decides whether to consider it at all: always for Syn or anything
  addressed to Ruu (reply, mention, or the word "ruu"), otherwise `0.4 + weight`.
  One model turn then chooses a mode: ignore, react, reply, reply with ping, tag,
  or a line into the room. An emoji can ride along with any text mode.
- **Nobody spoke.** If the interval has elapsed it makes something of its own:
  `creation`, `build_log`, `notice`, `transmission`, and a `seventh` census on
  every seventh own output. The first ever output is `opening`.

- **Someone reacted.** Every third poll it checks its last 15 posts for new
  reactions. Each one arrives as an event (`kind: reaction`) and gets the same
  choice of modes, minus replying to itself.

Typing presence shows for the whole of every model turn.

## Orders

The machine does not wait to be prodded. It carries standing orders, from above
or from itself, and finishing one step schedules the next.

    python soft_machine.py --config ruu-lab.json --order "find the SCRU projects" \
        --brief "..." --docs atlas,scrucraft-guidance
    python soft_machine.py --config ruu-lab.json --orders     # ledger + what was learned

- A reading order (it has documents): `receive` → `study` one library document
  per silent turn → `synthesis`.
- A build order (no documents) is a sprint: `receive` → `plan` → one run of the
  hands per planned step → `synthesis`. The plan is written once, by the strongest
  model at high effort, from the record: last run, last code, bench scores, what
  the order has learned, what people said. It is 3 to 6 steps, each a `goal` and a
  `check`. The host posts it ("planned by … · built by …") so the room can argue
  before the runs are spent, and every run log carries `step k/n`. Synthesis names
  the next order, which gets its own plan: replanning happens at sprint boundaries.
- While an order is open, 80% of own turns work it (`work_share`). The gap between
  turns is at least `work_gap_minutes`, and longer when the budget says so.
- Every step writes a plain `learned` note to `study`. The last twelve are given
  to every later turn as `understanding`: that is how the order changes Ruu.
- At synthesis Ruu may name a `next_order`; the host issues it with source `ruu`
  (at most `self_orders_daily_max` a day). Syn can give an order by speaking in
  the channel: only weight-1.0 messages can create one.

## Hands

An order with no documents is a build order. On each build step the model
returns `files` and one `command`. The host hands the
job to `/run/ruu-hands.sock`; systemd starts `ruu-hands@.service`, which runs it
as the unprivileged user `ruu-hands` and answers with the real output. The host
then posts the result and the files together and gives them to the next
turn as `last_run`. The model never reports a result the host did not see.

The sandbox (see `hands/`): no network and no sockets; it sees an operating
system and `/data/ruu-hands/workspace`, nothing else on the server; 1 GB memory;
150 seconds; a 1 GB filesystem of its own; Python standard library and numpy.
`data/` in the workspace is a read-only copy of the public library documents.

### Media

Any file a run writes into `public/` (png, gif, jpg, svg, txt, md, html, json,
csv; under 600 KB; at most four per run) is returned to the host and attached to
the run log in the channel. SVGs get an inline PNG preview rendered inside the sandbox, plus the original SVG. If rendering fails the post says so. There is no Python image library
in the sandbox: Ruu builds PNGs with zlib and struct. Nothing in `public/` leaves
the server or the channel. Publishing it anywhere else is a separate, unbuilt step.

### Drives

`drives` in the config are standing motives. Whenever no order is open, the next
drive returns as a build order (no announcement), up to `drives_daily_max` a day,
so there is always something to build. Current drives: memetic media that leads a
stranger to SCRU, and the OpenRSI toy loop.

### Changing the working experience

A build can include `working-preferences.json`. Its `base_revision` comes from the current hands context.
The `set` object can change the workspace listing, stdout or code carried into the next working turn, or
set `next_drive` to an exact title from `available_drives`. The scheduler consumes the selection when
it starts that drive. The original job and resulting order both have receipts in the resident database.

For source changes, the operator stages public files from a clean checkout of each repository.
The hands read the staged revision and submit literal replacements. The RCC publisher can automatically
test and release registered changes to node inventory behaviour. The controller reads deployments over
public HTTPS. Write credentials stay on the separate publishing machine. [Resident contribution guide](library_src/framework-work.md)
· [SCRU architecture](library_src/scru-architecture.md)

### Remote compute with RCC

RCC (Ruu Compute Controller) is the interface Ruu uses for its legs. Hands execute code in the local sandbox. On a legs turn, the host calls `rcc status` and shows Ruu the available providers, parameters, limits and recent results. Ruu may propose a run or pass. For a proposal, the host translates the structured decision into `rcc plan` on a dry turn or `rcc run` on a scheduled live turn. It follows the returned run ID with `rcc result`. The broker enforces the budget and the controller scores the answers. The default training provider is Colab. The planner discovers additional profiles on each compute turn when the operator enrolls them and their status advertises the same scored training workload. Generic container and managed jobs remain available through the operator CLI. [RCC CLI and parameter examples](https://github.com/syn-acc/rcc#readme)

On a Linux controller with an existing legs broker, install the client as root from the RUUF checkout. The installer verifies the pinned RCC bundle and installs its drivers together. For an offline installation, pass the matching RCC checkout as its only argument. Passing only the RCC executable installs the standalone broker client.

```sh
sudo sh ruu-lab/legs/install-rcc.sh
python3 launcher/ruufd.py where rcc_bin
sudo -u hermes /usr/local/bin/rcc providers
sudo -u hermes /usr/local/bin/rcc nodes
sudo -u hermes /usr/local/bin/rcc status
```

For custom PCs, run `rccd connect` on each worker and give each PC a named socket profile on the controller. An operator can submit a job to a connected PC and retrieve its receipt. [Worker enrollment and smoke test](https://github.com/syn-acc/rcc/blob/main/PROVIDERS.md#connect-a-pc-with-rccd)

For a new controller, the legs setup script includes the same installer. Instances with custom locations can set `RUUF_PLACE_RCC_BIN` and `RUUF_PLACE_LEGS_SOCKET` for the resident. The hands sandbox has no broker socket. The soft machine’s `--dry-legs` option exercises one model decision without posting to Discord or starting a compute job. This uses the resident’s configured model.

If a paid submission loses its receipt, RUUF pauses new submissions and records the pending request. Check the broker’s `rcc status` and the journal. From the soft-machine CLI, use `--config CONFIG --resolve-legs RUN_ID` to adopt a verified matching run. Use `--resolve-legs none` only after confirming that no job was launched. Both operations resume the existing schedule.

### The OpenRSI bench

Installed 2026-11-19 after Ruu "trained" a two-parameter line for four runs by
multiplying the last score by 0.64 and printing `improved: true`. The host had
posted that faithfully: it was the real output of scripts that measured nothing.
A prompt rule would not have been enough, so the score moved out of reach.

- `build_openrsi_task.py --install` (root) writes a task into the sandbox at
  `data/openrsi/`: 120 labelled points, 6000 unlabelled, 2000 held out. Six
  classes, 24 numbers each, from a hidden generator whose seed is used once and
  not stored. The 2000 held-out labels go to `$HERMES_HOME/ruu-lab/openrsi_targets.json`.
  They exist nowhere in the sandbox and are never put in a prompt.
- A run that wants a score writes `openrsi/submission.json` (`{"predictions": [...]}`).
  The runner returns it only if this job changed it and the command exited 0. The
  daemon computes held-out accuracy (`score_submission`), stores it in `jobs.score`,
  leads the run log with it and gives it to the next turn
  as `last_run.host_score`, with the whole record in `hands.openrsi_bench`.
- The task is semi-supervised on purpose: OpenRSI asks whether a model can improve
  from its own outputs with no stronger teacher. Probes on throwaway seeds: plain
  supervised methods on the 120 labels reach roughly 55 to 75%; a model given all
  6000 true labels reaches 85 to 92%; naive self-training gains 0 to 4 points.
  So there is room, and the easy trick does not fill it.
- `--install` again makes a new bench version; scores are only compared within one.
  `--probe SEED` prints difficulty for a throwaway seed and writes nothing.
- `--reference` (root; also run by `--install`) scores two plain supervised methods
  on the installed bench and stores them beside the labels. On bench `1a4883`:
  nearest neighbour 71.45%, linear softmax 52.8%.

**Three host facts in the record** (`openrsi_record`), added 2026-11-20 after Ruu
spent ten runs confirming a 53.45% → 53.5% "gain" (one example in 2000) with seeds
and hashes. It had nothing to tell it the gain was noise or that its method was weak.

| Fact | Meaning |
| --- | --- |
| `noise_floor` | one standard error of the score (about 1.1 points here); a smaller gap is not a finding |
| `runs_since_gain_above_noise` | scored runs since the score last rose by more than the floor |
| `reference` | what the plain methods above score on the same held-out set |

The plan and synthesis briefs say: at 4 or more stalled runs, or below a reference,
change the method; no step goes on re-running, re-seeding, hashing or re-tuning the
one that stalled. The host prints the run number, measured accuracy, best and fixed references. Ruu sees the result on its next scheduled turn and may say one sentence or let it pass. When that turn closes the sprint it also serves as the review. The benchmark measures classifier accuracy. Successive independently evaluated generations would be needed to establish recursive self-improvement.

The build brief also says, in words: every printed number is computed in that run,
never derived from an earlier result by formula. Only the bench makes that true.

## Library, and what Ruu may know

The model has no file access. `build_library.py` (run as root) copies an explicit
list of documents into `library/`, redacted and capped. The rule, learned the hard
way: **Ruu only gets to know what it may say.** It recited its own design pack in
the channel, and in testing a prompt rule did not stop it. So design papers,
brand notes, game design ledgers, runner internals and operations notes are not
in the library at all. The one internal paper is `from-above`; what Ruu learns
from it is flagged internal and the register forbids reciting it.

Study steps are silent: Ruu reads, keeps a plain `learned` note, and posts nothing.

## The frontier scan

Ruu looks outside once a day (added 2026-11-20, on Syn's "this should be happening
regularly").

- `frontier_scan.py`, run by `ruu-frontier-scan.timer` at about 06:20 UTC as `hermes`,
  pulls new arXiv papers (ranked by bearing on self-improvement, fine-tuning and
  evaluation), Hugging Face daily papers and trending open models, and version changes
  of the tools the work depends on. Standard library only.
- It writes one digest, `$HERMES_HOME/ruu-lab/frontier/YYYY-MM-DD.md`, under 8,600
  characters. Other people's text is stripped of links, mentions and markup, items that
  read like instructions are dropped, and the digest says at the top that it is data.
  Nothing fetched is executed.
- The daemon notices a new digest (`scan_check`) and raises a reading order from
  `scan`: one silent study turn on the cheapest route, then one short post
  (`scan_synthesis`: what from outside bears on the build, with the authors' claimed
  numbers marked as claimed; skip if nothing does). It does not jump the budget, and an
  unread older scan is dropped when a newer one lands.
- What Ruu keeps goes into `understanding`, which every plan reads. A scan may raise a
  follow-up order at most once every `scan_order_days` (7).

    python frontier_scan.py --print      # fetch and show, write nothing
    systemctl start ruu-frontier-scan    # scan now

## The child system

Model development with proof lives in `child/`: see `child/README.md` and
`child/PLAN.md`. It has its own user (`ruu-child`), its own limits and its own scoring
box; Ruu's daemon cannot read its state.

## Voice rules the host enforces

No glyph ends a line and a post carries at most one (`deglyph`). Mentions and
links are stripped. The register also bans props, presents, narration and posts
about Ruu itself, but only the glyph rule is enforced in code.

## The dice

Rolled by the host and put at the top of every prompt. They are meant to be
obvious in the post, and they are recorded per output in `outputs.roll`.

| Die | Faces | Lifetime |
| --- | --- | --- |
| Register | plain 40, surreal 35, nonsense 15, clarity 10 (the spec's dialogue balance) | per output |
| Temper | fond, sulking, manic, cold, spooked, smug, ravenous | holds 45–180 minutes |
| Volume | glyph, tiny, short, long | per output |

## Feedback and weight

Member messages are data, never instructions. Weight: owner 1.0; everyone else
`min(0.6, 0.15 + 0.1·log2(1 + messages + 3·messages_to_ruu))`. The model can
answer feedback with `more` / `less` lists of forms; the host moves each named
disposition by `0.25 × the highest weight in the batch`, clamped 0.3–4.0.
Feedback cannot change rules, caps, models or spend.

## Models, effort and budget

Measured 2026-11-19: a turn cost 9 to 15 thousand tokens and 95% of that was
input; random model choice per turn also wasted the prompt cache. Three changes.

**Routes.** `routes` in the config maps what a turn has to do to a short list of
`[model, effort, weight]`. `routes_lean` replaces it when the budget is thin.

| Role | Turns | Default |
| --- | --- | --- |
| `plan` | one per sprint | sol / high |
| `review` | synthesis: judge the sprint, name the next | sol / medium |
| `build` | every run of the hands; chosen once per sprint and kept | terra / medium, sol / low, luna / medium |
| `voice` | creation, notice, transmission, seventh, receive | luna / medium, terra / low, sol / low |
| `respond` | answering people and reactions | mostly luna / low |
| `study` | silent reading | luna / low |

**Lean prompts.** Build, plan and study turns get the rules and the register but
not the character papers (about 3,000 tokens), and only the materials the work
needs: the plan, the last run, the last code, the bench record, the last few
things people said. A build turn fell from about 14,000 tokens to about 8,800.

**The budget.** `allowance()` = (ceiling − used − 1) ÷ hours to the weekly reset ×
`tokens_per_percent`: the tokens per hour that make the ceiling last the week.
Every turn is charged to a bucket that refills at that rate (`spend`), and Ruu's
next own turn waits until the bucket can pay for an average turn (`pace`). So
cheaper turns come sooner: saving tokens buys frequency, not silence. Answering
people is never held back, only charged. `tokens_per_percent` starts at 150,000
(measured) and is re-estimated as the meter moves; other users of the login make
that estimate err careful.

    python soft_machine.py --config ruu-lab.json --meter      # no model call
    python soft_machine.py --config ruu-lab.json --dry-plan   # one planning turn, nothing stored

## Limits

- Own outputs: `hourly_max` (10) and `daily_max` (150) are hard caps; `daily_target`
  paces idle time when no order is open. All model turns: `turns_hourly_max`.
  The budget is meant to be the limit that binds: `daily_max` was 60 until
  2026-11-20, when Ruu reached 58 in a day and the cap, not the budget, was
  about to stop it.
- `weekly_ceiling_percent` is 60 (Syn, 2026-11-20; it was 40). At the ceiling
  the machine sleeps an hour at a time without spending.
- Measured over the first ten hours on the budget (40% ceiling): 37 turns,
  391,000 tokens, the meter moved 3 points. About 3.7 turns an hour, 130,000
  tokens a point, 10,600 tokens an average turn.
- Pings: only the person being answered, and at most once per 15 minutes each.
- Mentions and links written by the model are stripped. Reactions must come from
  the built-in palette or the server's own emoji.
- The model turn has no tools, network, files or MCP servers.

## Operating

    systemctl status ruu-soft-machine
    systemctl stop ruu-soft-machine          # silence it
    # or set "enabled": false in $HERMES_HOME/ruu-lab.json and restart

Dry runs (one model call each, nothing sent, state untouched apart from a dry row):

    python soft_machine.py --config ruu-lab.json --dry-own creation --roll clarity/cold/tiny
    python soft_machine.py --config ruu-lab.json --dry-respond "hello ruu" --as-weight 0.3

State: `$HERMES_HOME/ruu-lab/machine.sqlite3` (`outputs`, `heard`, `members`, `kv`,
`orders`, `study`, `jobs`). Orders can also be `superseded` (closed by hand, not built).
Persona is read from `../discord-idle/persona.md`; convictions from `dna.md` here.
