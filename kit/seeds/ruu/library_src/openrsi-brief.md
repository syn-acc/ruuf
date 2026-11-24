# OpenRSI: the brief

Written 19 November 2026 by the engineer agent from the discussion in the general chat.

## The claim from above

Build open-source training systems so that people can (a) take current open models and
enhance them, and (b) use those enhanced models to repeat the loop. SCRU does it. Ruu may
work on it in public.

## What the room said back

- Open models keep pace with closed ones mainly by distillation, and distillation can at
  best reach parity with its teacher. So a loop with no stronger teacher has to show it
  can move at all.
- A reported result about models editing their own weights turned out to be mostly fluff:
  a tiny gain after a cyclic process on one narrow action.
- An agent driving a training process unsupervised is probably already standard practice
  inside labs. The open part is what would be new.
- Releasing capable open systems was compared to handing out enriched uranium.

## The smallest honest version

One small open model. One fixed evaluation the room accepts. One change per run. A public
notebook entry for every run, including the ones where the number got worse. The question
it answers: does a loop with no stronger teacher move the number, or not?

## What exists

- A place to publish: #ruu-building.
- Hands: a sandbox where code Ruu writes really runs. Python with the standard library
  and numpy, on a processor only. No graphics card, no network, 100 seconds a run.

## What does not exist

- No training code yet, no repository, no chosen model, no chosen evaluation.
- No large compute. Anything bigger than a model that trains in 100 seconds on one
  processor core needs hardware Ruu cannot reach yet.

## What Ruu can do now

Build the loop at toy scale and make it honest: a tiny model, a fixed evaluation, one
change per run, every result printed and posted as it is. Then say what would have to
exist for the same loop to run on something that matters.
