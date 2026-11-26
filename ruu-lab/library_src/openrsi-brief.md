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

Ruu has a local CPU classifier bench in its hands workspace. It can change a method and submit predictions for scoring against labels held by the host.

RUUF also contains a child-training experiment with Qwen3.5-4B. Ruu can plan a compute turn through RCC. The Colab worker generates attempts, trains a child and returns answers. The host scores them against a recorded baseline. The child experiment log records the comparison, sample size, and uncertainty. Some gains were measured on families used for training. Showing that an improved child can produce a better descendant is a separate experiment.

The available scored providers and recent receipts come from RCC on each compute turn. A connected PC can run ordinary jobs through rccd. To become a training provider it also needs the host's training and scoring workflow.

[Child experiment log](https://github.com/syn-acc/ruuf/blob/main/ruu-lab/child/README.md) · [Experiment and evidence criteria](https://github.com/syn-acc/ruuf/blob/main/ruu-lab/child/PLAN.md)
