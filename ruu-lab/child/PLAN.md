# Ruu's child: a model Ruu improves, with proof

Plan written 2026-11-20 from Syn's brief: Ruu maintains a child system that takes
the best open model available, applies our RSI updates on Colab, and tries to prove
the needle moves on coding, logic and general capability.

**This is the plan. What is actually built and tested is in [README.md](README.md).**
As of 2026-11-20: stage 0 is live; stage 1 has a scored baseline for one model
(Qwen3.5-4B, 58.2% on the logic dev set); stage 2 has trained and scored four
children. The first idea showed no gain. Training on checked replies to hard items
gained 4.8 and 7.9 points (two seeds) on the ten families trained on, a no-checker
control lost 3.9, and the other 34 families showed no change outside the noise. That
is rung 1 with its control; nothing has moved on held-out families. The name is a
placeholder.

## What the web check found (2026-11-20)

| Finding | Why it matters here |
| --- | --- |
| Colab has a command-line client (`google-colab-cli` 0.7.1, Sept 2026, Linux/macOS). `colab run --gpu L4 script.py` makes a GPU machine, runs the script and tears it down. Login is a paste-a-code flow that works on a server. `colab usage` prints the compute-unit balance. | uk can start Colab jobs itself after one login by Syn. No browser session to babysit. |
| Every self-improvement result that held up had a verifier outside the model, usually a code executor (Absolute Zero, PopuLoRA). The verifier-free one (G-Zero) collapsed in round 3; the self-judging one (MASS) gained on two benchmarks and lost on two. | The verifier is the design. Ruu's bench already taught this: the score must come from somewhere the model cannot write. |
| Published gains are +2 to +13 points over 2 rounds or about 200 update steps, with diminishing returns. Compute: days on a cluster, or 61 to 110 hours on eight H100s. Small models stall first (Absolute Zero's 3B plateaued; 7B and 14B kept going). | A Colab Pro+ month is roughly 100 A100-hours or 300 L4-hours. We cannot repeat those experiments. We need a cheap loop and should expect smaller gains. |
| Most of those papers report single runs with no error bars (one uses a 30-question test). | Careful measurement at small scale is the part we can do better than the literature. |
| LiveCodeBench's newest set (v6) ends April 2025, and LiveBench's newest fully public release is older. Every 2026 model was trained after both. | Public benchmarks can show relative change but not clean absolute skill. Fresh, generated tasks with hidden answers are needed. |
| Current small open models: Qwen3.5 (0.8B, 2B, 4B, 9B; Apache-2.0; March 2026), Gemma 4 12B (June 2026; Apache-2.0 reported), Ministral 3 (3B, 8B, 14B; Apache-2.0), Phi-4 14B (MIT), VibeThinker-3B (very high self-reported scores, licence unstated). Aggregator scores disagree wildly (Ministral 3 8B on LiveCodeBench: 61.6 on one site, 16 on another). | Trust no published number. Stage 1 measures the candidates ourselves. |
| Evaluation statistics are settled practice: paired per-item differences, clustered standard errors, power analysis (Miller 2024); avoid normal-approximation intervals under a few hundred items; `evalci` packages the tests. | Use them from the first run. |

## The loop

One generation, in order. "G0" is the base model, "G1" the model after one pass.

1. **Generate.** G0 answers thousands of fresh tasks, several attempts each (k = 8).
2. **Verify.** uk checks every attempt against answers and tests the GPU machine never
   sees: generated logic tasks by their checker, code by running hidden tests in Ruu's
   existing no-network sandbox.
3. **Keep.** Only verified attempts on tasks G0 usually fails become training data.
   This is the step that can lift a model above itself: eight tries plus a checker
   beat one try, and training teaches one try to do what eight did.
4. **Train.** A LoRA adapter on the kept data. That is G1.
5. **Measure.** G0 and G1 answer the same held-out items; uk scores both.
6. **Repeat from G1.** The claim "recursive" needs G2 > G1 > G0.

No stronger teacher is used anywhere, so a member's objection in the channel
("distillation caps at parity") does not apply; the limit is instead the verifier's
reach, which is why this starts with code and logic.

## What counts as proof

A ladder, fixed before any run. Each result is labelled with its rung.

| Rung | Claim | Test items |
| --- | --- | --- |
| 1 | Better at the trained kinds of task | same task families, unseen seeds |
| 2 | Better at kinds it never trained on | task families held out of generation |
| 3 | Better on outside benchmarks | LiveCodeBench lite, a public LiveBench release |
| 4 | It compounds | G2 beats G1 on rungs 1 to 3 |

Rung 1 is nearly certain and proves little. Rung 2 is the first result worth
announcing. Rungs 3 and 4 are the needle.

**A rung is passed when:** the paired difference on the same items has a 95% interval
clear of zero, a second training seed agrees, and the general-ability check (below)
has not dropped by more than two standard errors.

**Two controls, run with every generation:**

- *No verifier:* same loop, keep everything. Shows the gain comes from checking, not
  from more fine-tuning. The literature says this one should stall or fall.
- *Same compute at test time:* G0 with 8 attempts and a vote. Shows how much of the
  search gain training actually captured.

**Metrics reported for every run**

- Accuracy per suite and per task family, with n.
- Paired difference vs parent, 95% interval, and the count of items that flipped each way.
- Verifier yield (share of attempts kept) and duplicate rate of generated data.
- GPU-hours and compute units spent; units per point gained.
- Score by generation.

**Sample size.** With 15% of items flipping, 1,000 items resolve about 3.4 points and
3,000 resolve about 2 (my arithmetic, 80% power). Generated tasks make 3,000 cheap.

**Two test sets.** *Dev*: scored every run, visible to Ruu. *Sealed*: other seeds and
the held-out families, scored only when a generation is put forward. Choosing among
many runs on one test set manufactures gains; the sealed set is the guard.

## Short experiments first (Syn, 2026-11-20)

"The experiments should be shorter to go through hypotheses quicker and we promote
viability and scale up." So every idea climbs three tiers, and most die on the first.

| Tier | Size | Cost on an L4 | Passes when |
| --- | --- | --- | --- |
| Screen | 132 logic items (3 per family), one engine load shared by all the variants in the session | 2 to 4 minutes of generation per variant, plus about 5 minutes per session to start | the paired difference against the control is at least +8 points, or it halves the token cost at no loss. A screen can only show big effects, by design. |
| Confirm | full dev set (3,714 items), second seed for anything trained | 30 to 45 minutes | the 95% interval clears zero and the general-ability check holds |
| Scale | sealed set, larger model, both controls | hours | the proof ladder below |

Rules: a screen never touches the sealed set; a variant that fails its screen is
written down and dropped, not rerun bigger; several hypotheses share one session.
A session can now stay up between screens (`ruu-child open`), so the 5 minutes of
start-up is paid once an hour rather than once per screen. Built and tested against a
stand-in; not yet opened on real Colab. Each step still reloads the model (about a
minute and a half).

## Stage 2 as a screen (built and first run 2026-11-20)

One command, one session, one idea:

1. **Attempt.** The base model answers pool items (seen families, their own seeds,
   never a test question) several times with sampling on. Default: 8 items per family,
   4 attempts each.
2. **Verify.** uk scores every attempt. The session waits a few seconds.
3. **Keep.** `keep.py` chooses which of the model's own replies become training data
   and cuts each one off right after its answer. The choice is the only thing the
   verifier adds, so the control is made here too.
4. **Tune.** A LoRA adapter on the kept replies (rank 16, 2 epochs by default). The
   trainer reports the loss on one fixed slice three ways: base model, with the
   adapter, and with the adapter folded into the weights.
5. **Measure.** The child answers the screen's 132 dev items; uk scores them and
   pairs them with the baseline's answers to the same items.

**First idea to screen: learn to finish.** Keep the shortest attempt uk scored
correct (`verified-short`). The baseline says 21.7% of answers run out of tokens and
that finishing them at their family's usual accuracy is worth at most 7.5 points. If
the child wastes fewer tokens it should gain at the same cap, and because brevity is
not a family-specific skill, the held-out families are where to look.

**Controls.**

- *No verifier* (`unverified-short`): the shortest attempt that finished, right or
  wrong, cut to the same number of examples per family. Built. Run it beside any idea
  that passes its screen, not before.
- *Same compute at test time* (the base model with several attempts and a vote):
  not built.
- *A fresh base-model run in the same session*, to take session-to-session noise
  (about one point) out of a confirming run: not built; a screen uses the stored
  baseline.

Mine, not Syn's: every default above, and the choice of first idea.

**Result, 2026-11-20: no gain.** +1.5 points (−4.5 to 7.6) on the screen, answers no
shorter, cut-offs unchanged. Numbers are in the README. The design flaw: verified
replies come from where the model already finishes, so they cannot teach it to finish
where it does not.

**Next idea: a positive control.** Before any more ideas about what to train on, show
that the loop can move anything at this scale. Take the families where attempts
disagree (some right, some wrong on the same item: about ten families, 24% of pool
items), attempt more items there with more tries, keep only verified replies to items
the model fails at least half the time, and test on those same families' dev items
(80 each, so several hundred items and a resolution near 4 points). That is rung 1:
it proves little if it passes, but if it fails, self-training with a verifier is not
viable on this compute and the plan changes. Needs a `--family` filter in `prepare`.
Estimate: 55 minutes on an L4, about 2 units.

**Positive control, fixed before the run (2026-11-20 17:50 UTC, Syn's yes).**

- Families, by rule: at least 5 of the 12 pool items had attempts that disagreed in
  the first screen. Ten qualify: time_intervals, propositional_logic,
  calendar_arithmetic, string_manipulation, rotate_matrix, family_relationships,
  spell_backward, products, maze, bitwise_arithmetic.
- Attempts: pool items 13 to 72 of each family (none used before), 4 tries each at
  temperature 0.7. More items, not more tries: a new item yields a usable reply more
  often per attempt than a fifth try at an old one.
- Kept: `verified-short --hard-only`, the shortest correct try on items right at most
  2 times in 4. Trainer settings as in the first screen (rank 16, lr 2e-4, 3 epochs).
- Test: all 799 dev items of those families, against baseline B1 on the same items
  (54.2% there). Sampled accuracy on these families' pool items was 48%, and 73% of
  items had at least one right try in four: that gap is what the loop can draw on.
- Pass: the paired gain's 95% interval by item is above zero. With 799 items that
  needs roughly +3 to +4 points. The by-family interval and the per-family table are
  reported with it; ten families make the by-family interval wide.
- No control arm in this session. If it passes, the no-verifier control is the next
  screen, because a pass alone does not show the checker did the work.

**Result, 2026-11-20 18:36 UTC: passed.** 54.2% → 58.9% on the 799 items, +4.8 points
(1.6 to 7.9), gained 103, lost 65, p = 0.004; by family 0.1 to 9.1. Trained on 127
replies. Numbers and the per-family table are in the README. Three things it does not
show: that the checker did the work, what happened to the 34 families it was not
trained on, and that it repeats. One family fell (products, −11.4): the child stopped
writing out its multiplication. Likely cause, not isolated: 3 of the 5 products
replies it trained on give the result with no steps.

**Next: the control and the side effects, one session.** Mine, not Syn's.

1. *No verifier*: `unverified-short --hard-only`, matched to the same number of
   replies per family from the same attempts; tune; test on the same 799 items. If
   this child gains as much, the checker is not what moved the score.
2. *Repeat*: train the verified child again from the stored training file and test
   it on the 799. A second +3 or more makes a fluke unlikely.
3. *Side effects*: that same child on 10 dev items from each of the other 34 families
   (340 items; 12 of those families are held out). A loss there is the price of the
   gain; a gain on the held-out ones would be the first look at rung 2.
4. Keep both adapters on uk (about 130 MB each). Needs a download step in the
   launcher; until then every child dies with its session.

Estimate: 55 minutes on an L4, about 1.8 units.

**Fixed before the run (2026-11-20 21:57 UTC, Syn's yes).**

- Both children use trainer seed 1 (the first verified child used 0) and the same
  settings otherwise. The verified child trains on the same 127 replies as before.
- *The checker mattered* if the verified child beats the control child on the 799
  items with the paired interval above zero. If the control gains as much over the
  baseline, the +4.8 was not the checker's doing.
- *It repeats* if the retrained verified child's gain over the baseline again has
  its interval above zero.
- *Side effects*: 340 items resolve about 6 points, the 120 held-out ones about 10.
  A fall with its interval below zero is a cost. Anything else is "no cost seen at
  that resolution", not "no cost".
- Order: the verified child first (train, then 799 + 340 in one engine load), then
  the control (train, 799). Adapters are fetched after the measurements.

**Result, 2026-11-20 22:49 UTC.** All three readings came out the way that supports
the loop. Numbers and tables are in the README.

- The checker mattered: verified child +11.8 points over the control (8.5 to 14.9).
  The control fell 3.9 below the baseline.
- It repeats: the retrained verified child gained 7.9 (4.8 to 11.0).
- No cost seen on the other 34 families at about 5 points of resolution, and no
  gain on the held-out ones (0.0, −5.8 to 5.8).

What that leaves standing: rung 1 with its control, on ten families, for one model.
What it adds to the design rules: the trainer's seed is worth about 3 points, so two
children closer than about 5 points need more than one seed a side.

**Next: a second generation on the same ten families.** Mine, not Syn's. The brief
asks whether updates compound; so far there has been one round.

1. Build, no spend: put a kept adapter back on a new VM and fold it; let a child
   attempt pool items it was not trained on (today `prepare` refuses a child the
   whole pool); make one training file from two attempt runs.
2. Run: the first-generation child (seed 1, 62.1%) attempts the pool items not used
   yet in the ten families (about 470, 4 tries each). uk keeps its checked hard
   replies. A second generation is trained from the base model on those plus the
   first 127, with two seeds, and tested on the 799.
3. Reading, to be fixed before the run: the two second-generation children against
   the two first-generation ones (58.9% and 62.1%). If the second round adds
   nothing, compounding is not there at this scale. If it adds more than the seed
   noise, the next control is the same amount of extra data from the base model's
   own attempts, to separate "more data" from "a better generator".

Estimate: 80 minutes on an L4, about 2.5 units.

**Later, from the products finding:** products fell in all three children that
trained on it, checked or not, and one of them kept writing its working out. So
"shortest correct teaches skipping steps" explains part of it at most. Before
changing the pick, look at what the products replies have in common.

## Benchmarks

| Suite | Source | Use |
| --- | --- | --- |
| Logic and maths, generated | `reasoning-gym` (100+ task generators with checkers, Apache-2.0), seeded | training tasks, dev, sealed; some families held out |
| Code, generated tests | problems with hidden tests held on uk | training tasks, dev |
| Code, public | LiveCodeBench lite (v6) | rung 3, relative change only |
| Mixed, public | LiveBench public release | rung 3, relative change only |
| General ability | small instruction-following and knowledge sample | forgetting check |

## Base model

Start with **Qwen3.5-4B**: Apache-2.0, small enough that a whole generation fits in
about three L4-hours (estimate). Stage 1 also measures Qwen3.5-9B and Gemma 4 12B;
if a rung-2 gain appears at 4B, repeat it at the larger size, where the literature
says gains are bigger. VibeThinker-3B is not a base: licence unstated, and it was
already trained with self-distillation, so less is left to find.

## Who holds what

| Part | Where | Holds |
| --- | --- | --- |
| Ruu (planner) | uk, existing daemon | writes an experiment *spec* per sprint step: model, method, settings. No GPU code. |
| Launcher | uk, its own system user | Syn's Google login for the Colab CLI; checks each spec against an allow-list and a unit ceiling; runs `colab run` |
| Worker | Colab L4 (later also the 3090) | our versioned script: generate, train, answer test prompts. Sees prompts only. |
| Scorer | uk, existing sandbox and host | hidden answers and tests; computes every score and interval; posts results |

Consequences: Ruu cannot spend past the ceiling or run arbitrary code on Syn's
Google account, and neither Ruu nor the worker can write a score. uk has 20 GB free on
`/data`, so only the current generation's adapter and small result files live there.

## Compute budget

Third-party figures: Pro+ gives 500 to 600 units a month; an L4 costs about 1.7 units
an hour, an A100 about 5.5, an H100 about 18. `colab usage` gives the real balance.
Proposed ceiling (mine, unconfirmed): **60 units a week**, about 35 L4-hours, paced
by the same bucket that paces Ruu's Codex turns. Ruu's planning turns cost Codex
tokens as now.

## Stages

| Stage | Build time | Result | Needs Syn |
| --- | --- | --- | --- |
| 0. Regular scan | 2–3 h | daily digest of new models, papers and benchmark releases in Ruu's library; Ruu reads it silently and may raise one order a week | no |
| 1. Measurement | 4–6 h | G0 scores for three candidate models on dev suites, with intervals, scored on uk and posted by the host | Colab login on uk; unit ceiling |
| 2. One generation | 4–6 h | G1 vs G0 on rungs 1 and 2, with both controls | no |
| 3. Ruu drives | 3–4 h | Ruu plans generations as sprints; sealed test at promotion; one table of lineage | no |

After stage 3, the integrations that make it real: the 3090 as a second worker, a
compute-matched replication at 9B or 12B, Ruu-written data recipes run in a sandbox,
and a public results page (a publish step; needs a yes).

## The regular scan (stage 0)

A daily timer on uk pulls titles and abstracts from the arXiv API, Hugging Face
trending models and daily papers, and release notes of the tools this plan depends on.
It writes one capped, link-free digest into Ruu's library. Ruu studies it on its
cheapest route and, once a week, a review turn may raise one order ("try X on the
child"). Web text is data: escaped, capped, never executed, never an instruction.

## Limits and what not to build

- Most likely outcome: rung 1 passes, rung 2 shows a small gain, rung 3 sits inside
  the noise for a while. That is still a real result if measured properly.
- A 4B model may stall after one generation, as the 3B did in Absolute Zero.
- Until rung 2 passes: no registry, no leaderboard, no site, no naming. The whole
  system is one queue directory, one launcher, one worker script and one table.
- The CPU bench stays as Ruu's cheap gym; the GPU loop becomes the main line once
  stage 1 runs.

## Sources

- Colab CLI: https://github.com/googlecolab/google-colab-cli · Colab MCP: https://github.com/googlecolab/colab-mcp
- Colab pricing (third-party): https://fastero.com/blog/google-colab-pro
- Absolute Zero: https://arxiv.org/abs/2505.03335 · SEAL: https://arxiv.org/abs/2506.10943
- PopuLoRA: https://arxiv.org/abs/2605.16727 · G-Zero: https://arxiv.org/abs/2605.09959
- MASS: https://arxiv.org/abs/2610.12176 · AIDE2: https://arxiv.org/abs/2609.26457
- VibeThinker-3B: https://arxiv.org/abs/2606.16140
- reasoning-gym: https://github.com/open-thought/reasoning-gym
- LiveCodeBench: https://github.com/LiveCodeBench/LiveCodeBench · LiveBench: https://github.com/LiveBench/LiveBench
- Error bars for evals: https://arxiv.org/abs/2411.00640 · small-sample caution: https://arxiv.org/abs/2503.01747 · evalci: https://arxiv.org/abs/2607.04429
- Qwen3.5-9B: https://huggingface.co/Qwen/Qwen3.5-9B/tree/main · Gemma 4 12B: https://www.analyticsvidhya.com/blog/2026/06/google-gemma-4-12b/
- Small-model roundups: https://www.bentoml.com/blog/the-best-open-source-small-language-models · https://tinyweights.dev/posts/best-small-language-models-2026/
