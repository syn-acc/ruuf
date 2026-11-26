# Working on RUUF

Keep resident identities, workloads, libraries, channel settings and host-specific deployment policy in the instance that owns them. A framework change should work for an independently defined resident. Run a focused test through the launcher or the kit when changing their contracts.

## Working with an instance

Select the operator's manifest using `--manifest FILE` or `RUUF_MANIFEST`. Use `check`, `where` and `status` to inspect it. Starting an instance requires the operator's own origin. The operator records that at a terminal with `origin ask`. Respect each instance's delegated authority when changing running parts, resource access or publication credentials. [Launcher guide](launcher/README.md)

## Publishing framework changes

Work on a branch. Use the release routine to prove and land the change.

```sh
python3 routines/ship.py status
python3 routines/ship.py prove BRANCH
python3 routines/ship.py land BRANCH
```

Configure the proof host, users and checks in the clone's private `.git/ruuf/ship.toml` using a `[ship]` table. Give the file mode 0600. The routine records the proof against the candidate tree and settings. Use the clone's configured public Git identity. Configure the operator's private identifiers in `.git/ruuf/private-words`, one per line. The routine checks commits before it updates the remote branch.
