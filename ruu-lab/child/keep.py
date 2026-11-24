"""Keep: turn a scored attempt run into training data. Runs on uk.

The model answered each pool item several times; uk scored every attempt against answers the GPU
machine never had. This file chooses which of the model's own replies it will be trained on. That
choice is the only thing the verifier contributes, so it is also where the control is made.

    keep.py make ATTEMPT_RUN [--policy verified-short] [--like TUNE_ID] [--hard-only]
                             [--rank 16] [--lr 1e-4] [--epochs 2] [--seed 0] [--batch 8] [--batch-tokens 8192]
    keep.py show TUNE_ID

Policies:
    verified-short     per item, the shortest attempt the host scored correct
    unverified-short   per item, the shortest attempt that finished with an answer, right or wrong.
                       With --like, the same number of examples per family as that tune: the
                       no-verifier control.

Only the pool split is accepted: dev and sealed items never become training data.
"""
import argparse
import collections
import json
import random
import time

from common import RUNS, allow, jsonl, new_run_id, open_db

POLICIES = ("verified-short", "unverified-short")
END = "</answer>"


def trim(text):
    """Cut a reply right after its last answer block. The lesson is to stop there."""
    end = text.rfind(END)
    return text[:end + len(END)] if end >= 0 else text


def attempts_of(db, run_id):
    """Every attempt of a run, with its text, grouped by item."""
    job = RUNS / run_id / "job"
    texts = {(o["id"], int(o.get("sample", 0))): o.get("text") or "" for o in jsonl(job / "outputs.jsonl")}
    by_item = collections.defaultdict(list)
    for r in db.execute("select item_id, family, sample, correct, answered, tokens, finish from results "
                        "where run_id=? order by item_id, sample", (run_id,)):
        text = texts.get((r["item_id"], r["sample"]))
        if text is not None:
            by_item[r["item_id"]].append({**dict(r), "text": text})
    return by_item


def choose(by_item, policy, hard_only=False):
    chosen = []
    for item_id in sorted(by_item):
        attempts = by_item[item_id]
        right = sum(a["correct"] for a in attempts)
        if hard_only and right * 2 > len(attempts):
            continue  # the model already gets this one more often than not
        if policy == "verified-short":
            usable = [a for a in attempts if a["correct"]]
        else:
            usable = [a for a in attempts if a["answered"] and a["finish"] != "length"]
        if usable:
            best = min(usable, key=lambda a: (a["tokens"], a["sample"]))
            chosen.append({**best, "right_of": (right, len(attempts))})
    return chosen


def match(chosen, like_counts, seed):
    """Cut `chosen` down to the per-family counts of another tune, at random but repeatably."""
    rng, by_family, out = random.Random(seed), collections.defaultdict(list), []
    for row in chosen:
        by_family[row["family"]].append(row)
    for family in sorted(like_counts):
        rows = sorted(by_family.get(family, []), key=lambda r: r["item_id"])
        rng.shuffle(rows)
        out += rows[:like_counts[family]]
    return sorted(out, key=lambda r: r["item_id"])


def make(run_id, policy="verified-short", like=None, hard_only=False, seed=0, **settings):
    rules, db = allow(), open_db()
    limits = rules["tune"]
    run = db.execute("select * from runs where run_id=?", (run_id,)).fetchone()
    if not run or run["status"] not in ("scored", "partial"):
        raise SystemExit(f"{run_id} is not a scored run")
    if run["split"] != "pool":
        raise SystemExit("training data comes from the pool split only")
    if policy not in POLICIES:
        raise SystemExit("unknown policy")
    spec = json.loads(run["spec"])
    tune = {"rank": 16, "alpha": 32, "dropout": 0.05, "lr": 1e-4, "epochs": 2, "batch": 8, "batch_tokens": 8192,
            "max_tokens": 2048, **{k: v for k, v in settings.items() if v is not None}}
    if not (1 <= tune["rank"] <= limits["max_rank"] and 0 < tune["lr"] <= limits["max_lr"]
            and 1 <= tune["epochs"] <= limits["max_epochs"]):
        raise SystemExit("rank, lr or epochs outside allow.json")
    by_item = attempts_of(db, run_id)
    chosen = choose(by_item, policy, hard_only)
    if like:
        other = db.execute("select spec from tunes where tune_id=?", (like,)).fetchone()
        if not other:
            raise SystemExit(f"no tune {like}")
        chosen = match(chosen, json.loads(other["spec"])["per_family"], seed)
    chosen = chosen[:limits["max_examples"]]
    if not chosen:
        raise SystemExit("nothing to keep: no attempt met the policy")
    prompts = {p["id"]: p["messages"] for p in jsonl(RUNS / run_id / "job" / "prompts.jsonl")}
    attempts = [a for rows in by_item.values() for a in rows]
    per_family = collections.Counter(row["family"] for row in chosen)
    tune_id = "t" + new_run_id(run["model"])
    job = RUNS / tune_id / "job"
    job.mkdir(parents=True)
    with (job / "train.jsonl").open("w", encoding="utf-8") as sink:
        for row in chosen:  # the conversation as it was asked, and the model's own reply: nothing from the answer key
            sink.write(json.dumps({"messages": prompts[row["item_id"]], "reply": trim(row["text"])}, ensure_ascii=False) + "\n")
    stats = {"items": len(by_item), "attempts": len(attempts),
             "attempts_right": sum(a["correct"] for a in attempts),
             "attempts_cut_off": sum(a["finish"] == "length" for a in attempts),
             "items_with_a_right_attempt": sum(any(a["correct"] for a in rows) for rows in by_item.values()),
             "items_always_right": sum(all(a["correct"] for a in rows) for rows in by_item.values()),
             "examples": len(chosen), "examples_right": sum(row["correct"] for row in chosen),
             "examples_from_hard_items": sum(r["right_of"][0] * 2 <= r["right_of"][1] for r in chosen),
             "mean_tokens_kept": round(sum(row["tokens"] for row in chosen) / len(chosen), 1),
             "mean_tokens_all_attempts": round(sum(a["tokens"] for a in attempts) / max(1, len(attempts)), 1)}
    out = {"tune_id": tune_id, "model": run["model"], "revision": run["revision"], "source_run": run_id,
           "policy": policy, "like": like, "hard_only": bool(hard_only), "seed": seed, **tune,
           "chat_template_kwargs": spec.get("chat_template_kwargs") or {}, "per_family": dict(sorted(per_family.items())),
           "stats": stats}
    (job / "tune.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    db.execute("insert into tunes(tune_id,created,model,revision,source_run,policy,examples,spec) values(?,?,?,?,?,?,?,?)",
               (tune_id, time.time(), run["model"], run["revision"], run_id, policy, len(chosen), json.dumps(out)))
    db.commit()
    return out


def show(tune_id):
    row = open_db().execute("select * from tunes where tune_id=?", (tune_id,)).fetchone()
    if not row:
        raise SystemExit(f"no tune {tune_id}")
    spec, meta = json.loads(row["spec"]), json.loads(row["meta"] or "{}")
    s = spec["stats"]
    lines = [f"tune {tune_id} · {row['model']} · {spec['policy']}{' (matched to ' + spec['like'] + ')' if spec.get('like') else ''}"
             f" · from {row['source_run']} · {row['status']}",
             f"  attempts {s['attempts']} on {s['items']} items: {s['attempts_right']} right, {s['attempts_cut_off']} cut off; "
             f"{s['items_with_a_right_attempt']} items had a right attempt, {s['items_always_right']} were always right",
             f"  kept {s['examples']} examples ({s['examples_right']} right, {s['examples_from_hard_items']} from items the model "
             f"usually fails), mean {s['mean_tokens_kept']} tokens against {s['mean_tokens_all_attempts']} over all attempts",
             f"  settings: rank {spec['rank']}, lr {spec['lr']}, epochs {spec['epochs']}, seed {spec['seed']}"]
    if meta:
        loss = meta.get("loss") or {}
        lines.append(f"  trained: {meta.get('steps')} steps in {meta.get('train_seconds')}s on {meta.get('device')}; loss on a fixed "
                     f"slice: before {loss.get('before')}, with adapter {loss.get('adapter')}, folded in {loss.get('merged')}")
    return "\n".join(lines)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("make")
    a.add_argument("run_id")
    a.add_argument("--policy", default="verified-short", choices=POLICIES)
    a.add_argument("--like")
    a.add_argument("--hard-only", action="store_true")
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--rank", type=int)
    a.add_argument("--lr", type=float)
    a.add_argument("--epochs", type=int)
    a.add_argument("--batch", type=int, help="examples per step at most")
    a.add_argument("--batch-tokens", type=int, help="padded tokens per step at most; lower it when the GPU runs out of memory")
    b = sub.add_parser("show")
    b.add_argument("tune_id")
    args = p.parse_args()
    if args.cmd == "make":
        made = make(args.run_id, args.policy, args.like, args.hard_only, args.seed, rank=args.rank, lr=args.lr, epochs=args.epochs,
                    batch=args.batch, batch_tokens=args.batch_tokens)
        print(json.dumps({"tune": made["tune_id"], **made["stats"]}))
    else:
        print(show(args.tune_id))
