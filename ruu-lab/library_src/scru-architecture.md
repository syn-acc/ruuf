# SCRU architecture

SCRU is the Synthetic Culture Research Unit. Ruu is its first resident agent. A reply to Ruu, an experiment Ruu proposes, and a patch Ruu writes should each come back to the same resident with a record of what happened.

## What owns what

| Part | Responsibility and source |
| --- | --- |
| SCRU | The community and its cultural and research programmes. `syn-acc` is the public account for RUUF and RCC. Ruu’s voice belongs to the resident. Each repository defines its own shipping process. [RUUF contributor guide](https://github.com/syn-acc/ruuf/blob/main/AGENTS.md) |
| RUUF | Carries the resident’s orders, memory, model routes, Discord interactions and local workspace. Its manifest maps the parts of an instance. Ruu’s persona and convictions currently live alongside the framework. [Manifest](https://github.com/syn-acc/ruuf/blob/main/ruuf.toml) · [Resident loop](https://github.com/syn-acc/ruuf/tree/main/ruu-lab) |
| Hermes | Supplies the general Discord gateway and tools. Within the configured guild, the intended rule assigns the home channel and its child threads to the resident. Hermes handles requests elsewhere. The published resident loop polls the main home channel. End-to-end replies in child threads still need verification. [Gateway integration](https://github.com/syn-acc/ruuf/tree/main/hermes) · [Discord polling](https://github.com/syn-acc/ruuf/blob/main/ruu-lab/soft_machine.py) |
| RCC | Runs an approved workload on an operator-configured compute profile and returns a receipt. An enrolled PC running `rccd` can report availability and accept jobs. [PC enrollment](https://github.com/syn-acc/rcc/blob/main/PROVIDERS.md#connect-a-pc-with-rccd) |
| OpenRSI | The research programme asking whether an improved open model can help produce a better successor. Its training and evaluation code currently lives in RUUF. The controller holds the answer keys, scores predictions and compares generations. [Research criteria](https://github.com/syn-acc/ruuf/blob/main/ruu-lab/child/PLAN.md#what-counts-as-proof) · [Recorded experiments](https://github.com/syn-acc/ruuf/tree/main/ruu-lab/child) |

## From a worker to a scored experiment

Ruu chooses the question. On a scheduled compute turn, RUUF asks RCC which training profiles the resident may use, submits once and follows the receipt. Colab implements the metered, controller-scored path. An additional worker becomes eligible for autonomous OpenRSI when its profile explicitly opts in, advertises `ruuf.openrsi.v1`, enforces a budget and returns predictions for controller scoring. A successful container exit establishes that the worker ran. The score and its uncertainty establish what changed against the baseline. [Training provider contract](https://github.com/syn-acc/rcc/blob/main/PROVIDERS.md#enroll-a-training-provider-for-ruu)

**Proposed evidence record.** Link each hypothesis and falsifier to the parent checkpoint, evaluation version and split, seed, compute receipt, retained artifact, cost, and paired comparison. The host can feed that record back to Ruu and cite it in a research report. The recorded gains on trained task families support a narrower claim than the programme’s goal of improvement across generations. [Controls and repeats](https://github.com/syn-acc/ruuf/blob/main/ruu-lab/child/README.md#control-repeat-and-side-effects-2026-11-20-the-checker-did-the-work)

## How Ruu changes the way it works

Ruu can change which standing drive it takes next, how many workspace files it sees on a build turn, and how much previous code and stdout it carries forward. It submits a versioned choice in a successful hands build. RUUF validates it, records the outcome in SQLite and uses the new values on subsequent turns. Choosing a drive preserves existing orders and consumes the choice when the scheduler creates the selected order. The receipt gives Ruu the order and build IDs. [Working preferences](https://github.com/syn-acc/ruuf/blob/main/ruu-lab/working_preferences.py)

For an RCC source change, Ruu reads a staged public revision and writes exact text replacements against it. The automated publisher currently covers worker inventory health, probe deadlines and parallelism. The publisher tests an empty inventory, an offline worker and a responding worker, then publishes a qualifying change. The controller verifies the revision and installs it. Ruu sees the resulting revision or rejection in its working context. Other source edits can be developed and checked against the staged revision. Each additional behaviour needs its own executable acceptance criteria before it can be published automatically. [Source staging](https://github.com/syn-acc/ruuf/blob/main/routines/framework_change.py) · [Publisher](https://github.com/syn-acc/ruuf/blob/main/routines/publish_resident.py) · [Deployment receipts](https://github.com/syn-acc/ruuf/blob/main/routines/sync_resident.py)

The UK resident applies working preferences directly. Repository publication uses the trusted Git identity on a separate machine. In this deployment the Mac polls the resident workspace while it is awake and online. The worker and the resident keep their existing permissions. The independent OpenRSI scorer owns the answer keys, comparators and results.

## Cultural programmes

The Singularity Cycle and scrucraft have a specific connection to Ruu. Its library imports public lore from the former and agent guidance and release notes from the latter. MINDWILD, The City and the Training Gym appear in its project atlas. Other SCRU work, including MECH, mathematics and the fiction broadcast desk, has its own purpose and authorship. A shared community gives these projects context while each project retains its own evidence and release process. [Library sources](https://github.com/syn-acc/ruuf/blob/main/ruu-lab/build_library.py) · [Project atlas](https://github.com/syn-acc/ruuf/blob/main/ruu-lab/library_src/atlas.md)
