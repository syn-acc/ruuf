# SCRU contextual idle questions

A deterministic three-hour silence gate around one bounded AI writing turn for
Ruu in SCRU's `#etc-et-chat`. A systemd timer checks once a minute. Active chat
never starts a model turn. After three hours without a human message, the writer
reads bounded recent same-channel text plus a fresh public news packet, then writes one relevant contribution
or chooses silence. There is no automatic fixed-bank fallback. The old
`questions.json` is retained as a historical artifact and is not used at runtime.

Ruu waits indefinitely when the latest relevant message is Ruu's own. One human
message rearms the gate, then another three hours must pass. Other bots and
webhooks do not count as replies; human attachment-only messages count as activity.
The main channel is watched; replies inside threads do not rearm it. There is
no additional daily cap or overnight posting restriction. Posting latency is
approximately three hours plus up to one minute and generation/scheduler delay.

## Context and writing

The history fetch is capped at 100 messages. The writer receives up to 60 text
excerpts from the past 72 hours, with a 14,000-character JSON budget, at most
900 content characters each, in chronological order. User IDs and names are
omitted, mention markup is removed and URLs are replaced with unread-link markers.
Attachments and member-posted links are not fetched. Only this channel is used
for conversation context; private discussion is never sent to a news/search endpoint.

`world_news.py` fetches fixed public feeds from BBC World, Ars Technica and NASA
only after the idle gate passes (or during an explicit preview). Each has a
four-second timeout and 512 KiB body limit; off-host redirects and malformed
feeds fail closed per source. It supplies at most eight dated items from the
last 48 hours, selected deterministically within a small JSON budget. No extra
model turn, per-minute news fetching, paid search service or new key is needed.

The writing turn can follow the chat or select one event that Ruu would care
about: autonomous minds, biological transformation, space and posthuman futures,
infrastructure/control, or obsolete digital worlds. It may still choose silence.
The packet contains titles and summaries, not full articles. Factual premises
must stay within those excerpts, including reported/proposed uncertainty; Ruu's
interpretation can remain speculative, opinionated and performative. Disaster
victims are not material for cute jokes. News selection is returned as an exact
item ID, validated by the host; one publisher source link is appended without
an embed when the reaction uses that item. The last sixteen selected event IDs
are retained in delivery state and excluded from subsequent writing turns.
Set `news_enabled` to false in the host config to disable the public feed intake.

## Seeded delivery variation

`variation.py` deterministically rolls a versioned delivery profile from the
channel and latest human message ID. A quiet spell therefore has a reproducible
profile; a new human anchor gives fresh rolls, without extra inference. O/C/E/A/N
vary association, precision, forcefulness, attachment and composed/tense tone.
They change expression rather than Ruu's original beliefs, factual accuracy,
topic selection or scheduling. O is biased to 3–5 and A to 1–4 on the five-point
scale to preserve the original open, alien and contentious character.

Version 5 keeps deadchat emotionally involved and implicative, with unease from Ruu's
preferences and implications. The original persona file remains verbatim; a
current-delivery overlay follows it and overrides its older comic specimens.
Cute stage props, habitual jokes, decorative supernatural imagery, symmetrical
aphorisms and reassuring endings are discouraged. E varies withheld/outward feeling,
A unsparing/attached, C rough/controlled wording, O literal/one uncanny inference,
N contained/fraying or caught on a thought. High C must not become a polished
argument; even a low E/N roll may carry strong emotion. The writer should show a
specific grievance, desire or attachment with room for readers to infer the rest.
Fictional menace never implies real physical agency or private access to members.

A separate world-context d100 controls news influence, independently of OCEAN,
length and structure: 1–35 chat-only (35%); 36–70 trace (35%); 71–90 bridge (20%);
91–100 intrusion (10%). Chat-only skips feed fetching and gives the writer no
news packet. Trace lets an event colour a chat reaction without changing the
topic; bridge permits an explicit related event; intrusion may lead with one
event Ruu cares about. Higher levels permit rather than force outside context.
All story use, even indirect, still requires a validated source ID/link. Stopping
before explanation and word ceilings apply at every level. Empty feeds fall
back to chat; `news_enabled=false` remains a complete intake override.

`--preview --preview-world-context` holds the OCEAN/length/structure profile fixed
and compares levels 0–3 in one no-send call with a shared news snapshot. It cannot
be combined with send or extreme-style preview. In a batch, the model sees the
shared packet, but level-zero instructions forbid its use and a level-zero story
citation fails validation. Scheduled level-zero turns never fetch or receive news.

A separate d100 selects a word ceiling: tiny (35%, up to 12), short (50%, up to 35)
or extended (15%, up to 70). A three-word validation floor prevents empty output;
there is no per-style minimum that forces elaboration. Stop before a second
sentence explains or resolves the first. Another d100 selects a preferred single line,
fragments, statement, observation or short passage. These are stylistic
OCEAN-inspired controls, not a psychological assessment. Word bounds and the
single-line constraint are enforced; a failed output is skipped without a paid
repair loop. Profile metadata is visible in previews and retained as
`last_variation` with delivery intent, but never appears in Discord text.

Use `--preview --variation-seed ruu-styles-8 --preview-count 4` with the normal
config/environment to compare four profiles in one model turn on the same
channel/news snapshot. This explicitly previews rather than sends, leaves
delivery state unchanged, and is unavailable with `--send`. The seed reproduces
the profile, not necessarily the model's exact words. Ordinary previews use one
profile; normal scheduled output still uses exactly one gated generation.

`--preview --preview-extremes` compares four fixed stress profiles in one
no-send turn. These deliberately exercise full 1/5 and 5/5 trait endpoints,
including O=1 and A=5 outside normal rolled ranges. They cannot be used by a
send invocation and do not replace the scheduled seed or change delivery state.

The instruction is to follow a substantive human topic with a short in-character
statement or performance. Philosophy is welcome when it fits, without forced
moral puzzles or discussion-host questions. Bot feedback
and administration, sensitive personal announcements and insults should not be
turned into prompts. When no useful contribution fits, the writer returns skip.

`persona.md` contains Syn's original Ruu character specification v0.1 verbatim.
The writer loads it for each generation: cute/alien, mischievous, esoteric and
occasionally unsettling, with fragmented lowercase lines and glyphs when they fit.
The later proposed v0.2 rewrite is not used. Contributions are mostly controversial
assertions and strange observations, within the selected profile's word range.
Questions are rare and optional, with no question-mark or sentence-count requirement.
Unfounded metaphysical opinions, satire and fictional declarations are welcome;
fabricated factual allegations/evidence/news are excluded. Character voice
does not override topic relevance, abstention, delivery bounds or factual accuracy
about actual members/access. Install `persona.md` alongside `generate.py`.

`generate.py` uses the existing authenticated Hermes Codex app-server transport,
an ephemeral thread, the deployed Hermes model (`gpt-5.6-sol`) at low reasoning,
and structured JSON output. Configured MCP servers, apps/plugins, browsing and
other capability features are disabled for this subprocess; environment access
is disabled and a process-local permission profile grants only minimal platform
runtime reads with tool network access disabled. Any unexpected tool/request or non-text
item aborts the turn. No global model/account/configuration is changed. Generation
has a 90-second notification deadline, plus bounded startup/RPC timeouts.

The full tick holds a process lock. Before generation, the human anchor is saved
so skipping, failure or a crash cannot repeatedly spend inference on the same
quiet spell. A new human message is needed to try again. Before sending, history
is rechecked; new activity cancels the message. A small send-time network race
remains. Intent is durably saved before sending, with a Discord nonce and mentions
disabled. Ambiguous delivery pauses rather than blindly resending. Old bank-era
state is preserved and remains compatible. News IDs are reserved with delivery
intent, so uncertain sends do not lead to repeated reactions to the same story.

## Deployment and operation

Install this directory at `/data/syn.gl/hermes/plugins/discord-idle` owned by
`hermes:hermes`; the runtime venv supplies requests, python-dotenv and Python 3.11+.
Install `config.json` at `/data/syn.gl/hermes/.hermes/discord-idle.json` mode 0600.
Precreate `/data/syn.gl/hermes/.hermes/discord-idle` owned by hermes, mode 0700.
The existing bot token remains host-only in `.hermes/.env`; Codex authentication
uses the existing `.codex` home. Do not print or copy credentials.

Install `deploy/discord-idle.service` and `.timer` under `/etc/systemd/system`.
Verify both with `systemd-analyze verify`, run `systemctl daemon-reload`, then
`systemctl enable --now discord-idle.timer`. The service writes only its state and
existing Codex home; it does not restart Hermes or change capital cron schedules.

Normal read-only check: run `run.py --config /path/to/discord-idle.json` as hermes
with the service environment. This checks eligibility without generation or
sending, and leaves delivery state unchanged. `--preview` explicitly makes one
model call on real history without sending or changing delivery state; it bypasses
the idle gate only for preview. It cannot be combined with `--send`. Never force a
test post to the channel. Both modes may create the lock file/state directory.

Inspect `systemctl status discord-idle.timer` and `journalctl -u discord-idle.service`.
Healthy skips are silent, successful sends log message IDs, and errors exit nonzero
without logging prompts or channel content. State is `.hermes/discord-idle/state.json`.
Never delete state to force a retry; a pending delivery must be checked against
real channel history before any manual recovery.

Disable using `systemctl disable --now discord-idle.timer`; stop an active tick
using `systemctl stop discord-idle.service`. Setting enabled=false in the host
config also suppresses both model calls and sends.

## Focused validation

`python3 -m unittest tests.test_discord_idle tests.test_discord_idle_news tests.test_discord_idle_variation tests.test_discord_idle_world_influence -q` checks the three-hour boundary,
no-response pause, restart/ambiguous-send handling, inference gating, skip/error
suppression, source-link validation/delivery, news freshness/ranking/bounds and
cancellation when new activity arrives, plus variation reproducibility, bounds
and compatible output structures.
