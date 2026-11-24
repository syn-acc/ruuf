# The child system

Ruu's model-improvement loop: an open model is measured, improved and re-measured, and
every score is computed on uk from answers the GPU machine never sees. The plan and the
reasoning are in [PLAN.md](PLAN.md). This file says what exists and how to run it.

## The baseline (2026-11-20, Qwen3.5-4B on a Colab L4, scored on uk)

This is G0: the number any trained child has to beat. Thinking off, concise prompt,
1,536-token cap, one attempt per item, exact answers only.

| Suite | Score | 95% interval | Items |
| --- | --- | --- | --- |
| Logic, all of dev | **58.2%** | 56.6 to 59.8 | 3,714 |
| Logic, families that may supply training data | 61.6% | 59.7 to 63.5 | 2,520 |
| Logic, families held out of training | 51.2% | 48.3 to 54.0 | 1,194 |
| Code, HumanEval+ (public, default prompt, 1,024 cap) | 79.3% | 72.4 to 84.8 | 164 |

Run ids are in `/data/ruu-child/baseline-runs.txt` on uk (`B1` logic, `B2` code, `B3`
the default-prompt control). One session, 65 minutes, about 2 units; 2.2 million
tokens at 844 a second.

**The concise prompt is confirmed.** On the 880 items both prompts answered: 48.9% →
59.3%, +10.5 points (7.6 to 13.4; 6.7 to 14.5 when whole families are resampled),
136 items gained, 44 lost. It holds in both groups (+10.3 seen, +10.8 held out). The
screen had said +12.9 on 132 items.

**Where the 41.8% of misses are:**

- 21.7% of all answers (806) ran out of tokens. 20% finished and were wrong.
- Six families are almost entirely cut off: cryptarithm (100 of 100), futoshiki (98),
  game of life (96), self reference (61), circuit logic (57 of 80), binary matrix
  (53 of 79).
- Some families are right when they finish and lose only to the cap: zebra puzzles
  (33 cut off, 100% right otherwise), shortest path (36; 92%), n queens (48; 90%).
  If every cut-off answer were as good as its family's finished ones, the score would
  rise 7.5 points (6.9 seen, 8.9 held out; the three families that almost never
  finish are left out). That is the ceiling for "learning to finish" at this cap, and
  it is optimistic: the items that finish are the easier ones. A right answer takes
  338 tokens on average.
- Some families finish and are wrong: bf (0%), count primes (2%), bitwise arithmetic
  (13%), spell backward (18%), tower of hanoi (14%). Brevity will not fix those.
- `ruu-child show RUN` now prints cut-offs and "right when finished" for every family.

**Rerun noise.** The same items asked again in another session flip about 5% of the
time (7 of 132, 11 of 220), netting about one point, although sampling is off
(temperature 0). A screen cannot see below that.

**Correction, 2026-11-20.** `compare` used the generators' partial credit for its
levels and exact answers for its gained/lost counts, so it disagreed with `show`. It
now uses exact answers throughout and prints partial credit beside them. The screen
table below is restated; no decision changed.

## The screens that led there

132 logic items each, against the default prompt on the same items (47.0% exact).

| Variant | Score | Difference |
| --- | --- | --- |
| **concise prompt** | 59.8% | **+12.9 points (5.3 to 20.5), gained 23, lost 6: promoted, then confirmed** |
| draft prompt | 48.5% | +1.5 (−7.6 to 10.6): no gain, but a third of the tokens |
| direct, no working | 34.8% | −12.1 (−21.2 to −3.0): dropped |
| thinking on, 4,096 cap | 35.6% | −11.4 (−20.5 to −3.0); 64% ran out of tokens; those that finished were right 98% of the time |
| thinking on, concise, 2,048 cap | 38.6% | −8.3 (−17.4 to 0.0): dropped at this budget |
| code, first 60 HumanEval+ | 90.0% (80 to 95) | the first 60 problems are the easy end; all 164 give 79.3% |

What they said:

- The score is "accuracy within a token budget", and a model that learns to finish
  is a real improvement. That is the first training hypothesis for stage 2.
- Thinking mode is accurate when it lands and far too slow to land. It is the
  expensive search; the fast mode is the policy to train.
- A prompt change is not model improvement. It sets the control: stage 2 must beat the
  model at its best cheap prompt, not at a bad one.

Speed and cost, measured: 750 to 850 tokens a second for the 4B model under vLLM
(137 under plain transformers for the 0.8B model), 1.94 units an hour for an L4, about
5 minutes per session to start (machine, engine install, model load). The two screens
cost 0.4 and 0.3 units.

## First training screen (2026-11-20): no gain

The idea: train the model on the shortest of its own attempts that uk scored correct,
so it learns to finish inside the token cap.

| Step | What happened |
| --- | --- |
| Attempts | 383 pool items, 4 sampled attempts each: 58.6% right, 17% cut off. 268 items had at least one right attempt. |
| Kept | 268 replies, 272 tokens on average (508 over all attempts). 52 came from items the model fails at least half the time. |
| Tune | LoRA rank 16, lr 2e-4, 3 epochs: 150 steps in 12 minutes on an L4. Loss on a fixed slice of the training replies 0.100 → 0.009, the same after folding. |
| Child against the baseline, same 132 items | 60.6% → 62.1%: **+1.5 points (−4.5 to 7.6), gained 9, lost 7. Inside the noise.** |
| By group | seen −2.1 (−8.3 to 4.2); held out +11.1 (0.0 to 25.0), which is 5 gained and 1 lost on 36 items |
| Tokens | 602 → 604 on average; cut-offs 27 → 27 |

Reading:

- The child fits its training replies and answers new items no shorter. The idea is
  dropped, as the screen rule says.
- It could not have worked as designed. Training data comes from where the model
  already finishes: 177 of the 268 kept replies are from items it always gets right,
  and the families that run out of tokens supply almost nothing (3 of 12 items each
  for circuit logic and binary matrix; none for bf, count primes or tower of hanoi).
- The held-out figure is six flips. It is not a result.
- Where several tries do beat one: 91 of the 383 pool items were right on some
  attempts and wrong on others, concentrated in about ten families (time intervals,
  propositional logic, calendar arithmetic, string manipulation, rotate matrix,
  family relationships). That is the material the loop needs, and the first screen
  diluted it.

What the run settled about the machinery (all unknown before it):

| Question | Answer |
| --- | --- |
| Training speed on an L4 | 4.9 seconds a step at up to 3,072 padded tokens; 12 minutes for 150 steps |
| Memory | one 1,900-token example overflowed 22 GB; with gradient checkpointing the peak is 17.8 GB |
| Does folding into 16-bit weights keep the adapter | yes: loss 0.0087 with the adapter, 0.0086 folded |
| Does vLLM load the child | yes, the folded weights load like any local model, in 55 seconds |
| A session that stays up | opened in 55 seconds (engine install 43), first model load 224; four steps ran on it |
| Cost | 43 minutes, 1.26 units |

Two failures on the way, both fixed in `tune.py`: the Colab runtime ships a torchao
too old for peft, which then refuses to build any adapter; and the out-of-memory
handler retried while the failed pass was still held.

Run ids: attempts `1120-1525110a-qwen35-4b`, tune `t1120-154557d9-qwen35-4b`, child
`1120-15461019-qwen35-4b`. The child's weights were not kept.

## Positive control (2026-11-20): +4.8 points on the families it trained on

The question: can the loop move anything at this scale? The families, sizes and the
pass mark were written into PLAN.md before the run. Ten families were chosen by rule
(at least 5 of 12 pool items with disagreeing attempts in the first screen).

| Step | What happened |
| --- | --- |
| Attempts | 600 new pool items (60 per family), 4 tries each: 51.3% of tries right, 9% cut off. 404 items (67%) had at least one right try; 209 were always right. |
| Kept | 127 replies: the shortest correct try on each item that was right at most 2 times in 4. Between 5 and 23 per family, 510 tokens on average. |
| Tune | same settings as the first screen: 120 steps in 10 minutes. Loss on the fixed slice 0.095 → 0.013, the same after folding. |
| Child against the baseline, same 799 dev items | 54.2% → 58.9%: **+4.8 points (1.6 to 7.9), gained 103, lost 65, p = 0.004.** By family 0.1 to 9.1. Passed the mark set beforehand. |
| Where the gain came from | 82 of the 103 gained items were ones the baseline finished and got wrong; 21 were cut off. Cut-offs 73 → 61, mean tokens 390 → 389. |

| Family (80 items each) | Baseline | Child | Gained | Lost | Mean tokens |
| --- | --- | --- | --- | --- | --- |
| bitwise_arithmetic | 11.2% | 26.2% | 13 | 1 | 512 → 415 |
| maze | 23.8% | 38.8% | 19 | 7 | 861 → 1,069 |
| rotate_matrix | 70.0% | 78.8% | 15 | 8 | 553 → 594 |
| time_intervals | 65.0% | 72.5% | 9 | 3 | 338 → 339 |
| propositional_logic | 61.2% | 66.2% | 12 | 8 | 564 → 556 |
| family_relationships | 76.2% | 80.0% | 9 | 6 | 18 → 12 |
| string_manipulation | 48.8% | 51.2% | 10 | 8 | 797 → 715 |
| spell_backward | 17.5% | 20.0% | 7 | 5 | 23 → 19 |
| calendar_arithmetic | 87.5% | 86.2% | 8 | 9 | 198 → 154 |
| products (79 items) | 81.0% | 69.6% | 1 | 10 | 35 → 14 |

Reading:

- This is rung 1: the child was trained and tested on the same kinds of task. It
  shows the loop can move a score here, which the first screen did not.
- Not shown by this run: that the checker did the work, what happened to the other
  34 families, and that it repeats. The next section answers all three.
- With any one family left out the gain stays between +3.6 and +6.5.
- Products fell, and what changed is visible: the child stopped writing out the
  multiplication (35 → 14 tokens) and now gets long ones wrong, for example 559 × 871
  answered `487089` where the baseline wrote the sum out and got 486889. The likely
  cause, not isolated: of the 5 products replies it trained on, 3 state the result
  with no steps, because the shortest correct reply to a hard multiplication is often
  a lucky direct answer. "Shortest" looks like the wrong pick for hard items wherever
  a direct answer is possible.

Cost: 45.7 minutes, 1.42 units. Run ids: attempts `1120-175020c7-qwen35-4b`, tune
`t1120-181456b9-qwen35-4b`, child `1120-18145676-qwen35-4b`. The child's weights were
not kept; its training file is on uk under the tune's run folder.

## Control, repeat and side effects (2026-11-20): the checker did the work

One session, readings written into PLAN.md before it opened. Two children were
trained from the same 2,400 attempts with the same settings and trainer seed 1:

- **Verified**: the same 127 checked replies as the positive control.
- **Control**: 127 unchecked replies, the shortest finished try on the same kind of
  item (right at most 2 times in 4), the same number per family. 26 of them happen
  to be right.

| Question | Result on the 799 items of the ten families |
| --- | --- |
| Does it repeat? | Verified child 54.2% → 62.1%: **+7.9 points (4.8 to 11.0), gained 115, lost 52.** By family 2.4 to 13.9. Yes. |
| Without the checker? | Control child 54.2% → 50.3%: **−3.9 points (−6.9 to −0.9), gained 58, lost 89.** Training on unchecked replies made it worse. |
| Verified against control | **+11.8 points (8.5 to 14.9), gained 138, lost 44.** By family 6.3 to 17.8. The checker mattered, by the rule set beforehand. |

| Other 34 families, 10 items each | Baseline | Verified child | Change |
| --- | --- | --- | --- |
| 22 seen families (220 items) | 65.5% | 63.2% | −2.3 (−6.4 to 1.4), gained 7, lost 12 |
| 12 held-out families (120 items) | 54.2% | 54.2% | 0.0 (−5.8 to 5.8), gained 6, lost 6 |
| All 340 | 61.5% | 60.0% | −1.5 (−4.7 to 1.8), gained 13, lost 18 |

| Family (80 items) | Baseline | Control | Verified, seed 0 | Verified, seed 1 |
| --- | --- | --- | --- | --- |
| bitwise_arithmetic | 11.2% | 5.0% | 26.2% | 25.0% |
| maze | 23.8% | 38.8% | 38.8% | 41.2% |
| rotate_matrix | 70.0% | 52.5% | 78.8% | 78.8% |
| spell_backward | 17.5% | 16.2% | 20.0% | 45.0% |
| string_manipulation | 48.8% | 43.8% | 51.2% | 56.2% |
| family_relationships | 76.2% | 76.2% | 80.0% | 82.5% |
| propositional_logic | 61.2% | 65.0% | 66.2% | 66.2% |
| time_intervals | 65.0% | 57.5% | 72.5% | 66.2% |
| calendar_arithmetic | 87.5% | 82.5% | 86.2% | 86.2% |
| products (79 items) | 81.0% | 65.8% | 69.6% | 73.4% |
| All 799 | 54.2% | 50.3% | 58.9% | 62.1% |

Reading:

- This is rung 1 with its control: on the families it trains on, the loop moves the
  score and the checker is why. It is not rung 2. Nothing moved on the held-out
  families.
- No cost was seen on the other families at this resolution (about 5 points). A
  smaller cost is not ruled out. Answers there got 9% longer (627 → 683 tokens) and
  9 more ran out of tokens; that is the thing to watch if training grows.
- The training seed alone is worth about 3 points. The two verified children saw
  identical data and differ by 3.1 (0.4 to 5.9); spell_backward is 20.0% in one and
  45.0% in the other. A difference between two children under about 5 points needs
  more than one seed a side.
- Not every gain is the checker's. Maze rises just as far in the control, which
  trained mostly on wrong maze answers.
- Products falls in all three children. The control and the first verified child
  stopped writing the multiplication out (15 and 14 tokens against 35). The second
  verified child kept its length and still lost 6 items and gained none, so missing
  steps explain part of it at most.

Machinery: `ruu-child fetch` brought both adapters to uk, 130 MB each in 32 seconds,
496 tensors, file length checked. The first tuned-child engine load in a session
took 204 seconds; the second took 54.

Cost: 52.7 minutes, 1.55 units. Run ids: verified tune `t1120-21554810-qwen35-4b`,
its runs `1120-215549c6-qwen35-4b` (799) and `1120-215549a9-qwen35-4b` (340); control
tune `t1120-2155481d-qwen35-4b`, its run `1120-21554990-qwen35-4b`.

## State (2026-11-20)

| Part | State | Evidence |
| --- | --- | --- |
| Item sets `logic-v1` (dev 3,714 items, sealed 3,653) | built on uk | 44 task families; rebuild test passes in a fresh process |
| Training pool `logic-v1/pool` (3,673 items) | built on uk | the 32 seen families only, own seeds; no question shared with dev or sealed; adding it left dev and sealed byte-identical |
| Scorer and statistics | tested | 22 checks: known intervals, false-alarm rate 12/200, wrong/missing/hidden answers score zero |
| Worker | runs on Colab under vLLM | four real sessions scored, the last one the full dev baseline; one engine load serves every run in a session |
| Launcher: allow-list, unit ceiling, always-stop, reaper | tested against a stand-in, then on real Colab | units read from the real balance; a dropped connection is retried (seen twice, recovered once automatically) |
| Launcher's stand-in tests | current | `tests/test_launch.py`, 44 checks: call order, ledger, ceiling, reaper, retry after a dropped connection, open sessions, the training screen, the family filter, fetching an adapter |
| Scoring box (`ruu-score.socket`) | installed, attack-tested | no network, no view of the server, nothing persists, bombs contained, outsiders refused |
| Code suite `code-v1` (HumanEval+, 164 problems) | built on uk, one model scored | 79.3% for Qwen3.5-4B; see "The code suite" below |
| Training screen (stage 2): attempts, keep, tune, child's answers | run on real Colab in three sessions, step by step on an open session | four children trained and scored: no gain; +4.8 and +7.9 points on the families trained on; −3.9 for the no-checker control (see above). The one-command form (`generation`) has only run against the stand-in |
| Trainer `tune.py` | ran four times on an L4 with the 4B model | 99 to 150 steps, 8 to 12 minutes each |
| Serving a child under vLLM | works from folded weights, four children | the adapter route is written and has never been tried |
| A session that stays up between steps | used three times on real Colab | open, the steps, close |
| No-verifier control, a repeat, side effects on other families | run once | checker mattered (+11.8 over the control); repeat held; no cost seen at about 5 points |
| Keeping an adapter after the session | half built | `fetch` used on real Colab: two adapters on uk, marked `kept`. Putting one back on a new VM is **not built** |
| A second generation, the vote control | **not built** | |
| Ruu-written specs (stage 3) | **not built** | |

## Where things are

| | Path on uk | Owner |
| --- | --- | --- |
| Code | `/opt/ruu-child/` | root; read-only to everything else |
| Limits | `/opt/ruu-child/allow.json` | root: models, GPUs, 3 h per job, 60 units a week |
| State | `/data/ruu-child/` (`items/`, `runs/`, `child.sqlite3`, `venv/`) | `ruu-child`, mode 700 |
| Google login for the Colab client | `/data/ruu-child/.local/share/python_keyring/` | `ruu-child`, mode 600; absent until `ruu-child login` |
| Front door | `/usr/local/bin/ruu-child` | root |
| Reaper | `ruu-child-reap.timer`, every 15 minutes | |
| Tests | `/opt/ruu-child/tests/` (`test_launch.py`, `test_scoring.py`, `test_scorebox.py`, `test_codesuite.py`) | run as `ruu-child` with `RUU_CHILD_HOME` set to an empty directory |

Ruu's daemon runs as `hermes` and can read none of `/data/ruu-child`. Source for all of
it is this folder.

## Running a measurement

```
ruu-child login                                   # once, interactive: prints a Google URL, takes the code back
ruu-child usage                                   # the account's unit balance, as Colab reports it
ruu-child prepare --model Qwen/Qwen3.5-4B --per-family 3 --style concise   # a screen: 132 items
ruu-child start RUN_A RUN_B RUN_C --gpu L4        # one session, one engine load; detached; journalctl -u 'ruu-child-job-*' -f
ruu-child show RUN_ID
ruu-child compare CONTROL_RUN VARIANT_RUN         # paired on the items both runs share
```

`--per-family N` takes the first N items of each family, so a small run is always a
subset of a bigger one and the two can be compared item for item. `--style` picks how
the logic items are asked (`default`, `concise`, `draft`, `direct`); `--thinking`
leaves the model's thinking mode on. See PLAN.md for the screen, confirm and scale
tiers.

`prepare` pins the model's exact revision, writes the prompts (questions only) and
refuses anything outside `allow.json`. The sealed split needs `--promotion`. `start`
refuses when the week's units plus the worst case for this job would pass the ceiling,
and stops the Colab session whatever the worker did. A session that outlives its
deadline is stopped by the reaper even if the launcher has died.

## Running a training screen (stage 2)

```
ruu-child generation --parent BASELINE_RUN          # detached; prints a unit name for journalctl -u
ruu-child keep show TUNE_ID                          # what was kept, and the trainer's own report
ruu-child show CHILD_RUN ; ruu-child compare BASELINE_RUN CHILD_RUN
```

`--parent` is a scored run of the base model; the child is asked the first `--screen`
items per family of what that run answered (3 by default, so 132 items; 0 means all
of them) and compared with it. In one session it attempts `--pool` items per seen
family `--samples` times, scores them here, keeps replies by `--policy`, tunes, and
has the child answer. A second `--policy` is tuned and measured as a control in the
same session. `--pool-offset` skips pool items an earlier generation used.
`--family A B ...` (also on `prepare`) limits both the attempts and the test to those
families; `--hard-only` keeps replies only to items the model got right at most half
the time.

What crosses to the GPU machine for training is one file of conversations: the
question as it was asked and the model's own reply that uk chose. Choosing a correct
reply does tell that machine the answer to that pool item. Pool items are never test
items, and `prepare` refuses to ask a tuned child the pool.

The child's folded weights live only on the VM that trained them and are gone when
the session stops. `ruu-child fetch TUNE_ID --session NAME` copies the adapter (the
small part that was trained) to `runs/TUNE_ID/adapter/` on uk and checks the file is
whole; the tune is then marked `kept`. Nothing puts a kept adapter back on a new VM
yet; that is needed before a second generation can start from the first.

A session that stays up between steps, for bring-up and for several screens in a row:

```
ruu-child open --gpu L4 --hours 1                    # prints the session name; the reaper stops it at the deadline
ruu-child colab RUN... --session NAME                # no new machine, no stop
ruu-child tune TUNE_ID --session NAME
ruu-child fetch TUNE_ID --session NAME               # after the measurements: bring the adapter home
ruu-child sessions ; ruu-child close NAME
```

An open session burns units whether or not anything is running in it.

## What the login grants

The Colab client asks Google for: identity, `colaboratory`, `drive.file` (files it
creates) and **`cloud-platform`**. The last is wide: it covers any Google Cloud project
on that account. The scopes are fixed in the client. The token is stored in a file only
`ruu-child` can read. If that reach is not acceptable on uk, the alternative is a
second Google account that holds nothing else.

## How a score is made

1. `items.py` generates each task from a seeded open-source generator (`reasoning-gym`)
   and stores family, seed, index and a hash of the question. `seen` families may later
   supply training data; `held_out` families never do.
2. The worker receives `{id, messages}` only and returns text.
3. `score.py` regenerates each task on uk, checks the hash, takes the last
   `<answer>` block outside any thinking, and scores it with the generator's checker.
4. A comparison is paired on shared items: difference, 95% bootstrap interval by item
   and by family, items gained and lost, exact McNemar p, and how small a difference
   that many items could have shown.

Three families were thrown out for not rebuilding identically (`codeio`,
`isomorphic_strings`, `polynomial_multiplication`). Any new family has to pass the
rebuild test first.

## The code suite

`code-v1` is HumanEval+: public and old, so every 2026 model has probably seen it. It
can show a relative change between a parent and a child, nothing about absolute skill.
It is here because it exercises the path stage 2 needs.

- `codesuite.py build` downloads the problems (the file's hash is pinned on first
  build) and runs each reference solution **in the scoring box** on up to 60 inputs.
  What those runs returned is kept on the host as the expected values.
- A model's reply is reduced to its last code block (completed from the prompt when it
  is only a function body) and run in the box on the same inputs. The box returns what
  the code returned; the host compares, with a tolerance for floats.
- The box (`scorebox/`, user `ruu-score`, socket open to `ruu-child` only) is the same
  shape as Ruu's hands sandbox but keeps nothing between jobs and is never given an
  expected value, a reference solution or a test. A program cannot pass by finding the
  answer, because the answer is not in there.

    ruu-child prepare --model Qwen/Qwen3.5-4B --set code-v1

## Smoke test (2026-11-20)

Qwen3.5-0.8B, 44 prompts (one per family), 256 new tokens, thinking off, on this PC's
CPU at below-normal priority (4 threads, about 5 tokens a second), then scored on uk:
5 of 44 right. It proves the path from items to a scored table works with a real model
of the target family. One item per family is not a measurement, and it says nothing
about speed on a GPU.

Two things it showed:

- With thinking off the 0.8B model often answers with no working at all. Qwen3.5 thinks
  by default, so the first Colab session should be a pilot of both modes on about 440
  items before the full baseline.
- Qwen3.5 loads with `AutoModelForCausalLM` (transformers 5.23) and warns that it is
  running slow reference kernels. On Colab that path gave 19 tokens a second on a T4
  and 137 on an L4, so the worker now uses vLLM, as the model card recommends, in its
  own Python environment on the VM (`on_vm.py`). The `hf` engine remains for CPU.
