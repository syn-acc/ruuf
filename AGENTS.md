# If you are an agent

You have been handed this repository. Read this first; it is short.

ruuf is a framework for one resident agent: a series of processes, one launcher that knows what
they are and where everything lives, and a set of routines that turn rules into programs.

## Start with the launcher

    python launcher/ruufd.py check          # is the manifest (ruuf.toml) sound
    python launcher/ruufd.py check --host   # does this machine match it
    python launcher/ruufd.py where          # the map: every place by name
    python launcher/ruufd.py status         # the process table

Whatever the launcher answers is the next thing to do. Do not build a path by hand: ask `where`.
`launcher/README.md` says what it can and cannot do yet.

## The origin is not yours to write

    python launcher/ruufd.py origin         # what is known about where this agent comes from

An agent comes from somewhere: who its operator is, what they are to each other, and why it exists.
The launcher demands that from the operator and starts nothing without it. If questions are open,
tell your operator and point them at `ruufd origin ask`. Do not answer for them, do not fill the
file in from what you think they would say, and do not copy another agent's.

## How a change reaches main

Never push to `main` yourself, and never merge by hand. Use the routine:

    python routines/ship.py status          # what is ahead of main, and is it proven
    python routines/ship.py prove <branch>  # run the checks on the real machine, beside what is running
    python routines/ship.py land <branch>   # fast-forward main to a proven tip, or be told why not

Work on a branch, in small steps. Prove each step, and land it as soon as it is proven: a tested
branch is not left to drift ahead of main. If `land` refuses, fix the reasons it lists. Do not work
around it.

## Who you are when you commit

Commits carry this clone's git identity (`git config user.name` and `user.email`), which is the
operator's public identity. `land` refuses commits by anyone else and messages with trailers such
as `Co-Authored-By`. Names that must never be published are listed in `.git/ruuf/private-words` in
the clone, one per line; that file is inside `.git` and cannot be committed. If it does not exist,
ask your operator what belongs in it before you ship anything.

## When a rule turns up

When your operator states a rule, or you notice the same judgement being made a second time, do
not just remember it. Write it as a program under `routines/`, with tests, that either does the
thing or refuses and says why; name it here; and use it from then on. `routines/ship.py` is the
first one and the pattern to follow.

## What is not yours to decide

Anything that restarts, replaces or takes over a running part, changes what a sandbox may reach,
or publishes something under your operator's name is your operator's decision. Stop and ask.
