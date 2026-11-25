# RUUF

RUUF gives a resident agent a place to work between conversations. Its first resident, Ruu, lives in the Synthetic Culture Research Unit (SCRU) Discord. Ruu can pick up an order in `#ruu-building`, keep working while the room is quiet and bring back something people can inspect. Files stay in its workspace. Orders, notes and run results carry over in SQLite.

## A build in Ruu's channel

Suppose the operator asks Ruu to draw a map from the community's project notes. Ruu can read the notes and plan a build in three to six steps, each with a goal and a check. On a build step, the model supplies files and a command. RUUF runs the command in a network-isolated sandbox. If it produces a map in `public/`, the host can attach it to the Discord run log alongside the command output. On the next turn, Ruu sees what ran and can revise the map. A usage budget paces the work between turns. [How orders, builds and attachments work](ruu-lab/README.md)

## Explore the checkout

From the repository root, these commands inspect the configuration without starting a service. Use Python 3.11 or newer.

```sh
python3 launcher/ruufd.py check
python3 launcher/ruufd.py where state
python3 launcher/ruufd.py status
```

`check` validates the manifest, `where state` resolves the path for Ruu's records, and `status` shows which process owns each part. In the SCRU deployment, systemd owns the services. The manifest names their units and paths. [Launcher guide](launcher/README.md) · [Manifest](ruuf.toml) · [Service units](deploy/units/)

## Running an instance

The repository contains the source and example configuration for the running SCRU installation. To set up another resident, start with the operator guide. The launcher’s `origin` command asks the operator to describe who they and the agent are to each other and why the agent is there. Ruu's persona and convictions are still hand-authored. The manifest declares a model ladder, while the running lab selects models from its per-role configuration. Credentials, live settings, member data and run history stay on the host. The seed kit is an older snapshot of the lab. [Operator and contributor instructions](AGENTS.md) · [Repository map](MAP.md) · [Seed kit](kit/README.md)

## Compute and OpenRSI

For OpenRSI experiments, Ruu can propose a change to an open model and compare its answers with a recorded baseline. A remote worker performs the training. The controller scores the answers against a key it keeps locally. In one recorded baseline, Qwen3.5-4B answered 51.2% of 1,194 logic items correctly across task families held out of training. That gives a later run a concrete comparison. [Experiment settings and score intervals](ruu-lab/child/README.md)

Ruu uses **Ruu Compute Controller (RCC)** as its legs. On a compute turn, RUUF shows Ruu the available experiments through RCC, submits the experiment Ruu chooses through the Colab broker and retrieves its scored result by ID. Operators can also connect their own PCs with `rccd`, inspect them with `rcc nodes`, or submit jobs to named cloud and cluster profiles. Ruu’s autonomous training loop selects profiles that supply the same host-scored training contract. [RCC](https://github.com/syn-acc/rcc)
