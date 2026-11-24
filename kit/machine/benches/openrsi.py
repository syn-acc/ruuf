"""The OpenRSI bench: a score the agent can earn and cannot write.

The task is installed by build_openrsi_task.py. The held-out labels live in the agent's state folder,
outside the sandbox, and are never put in a prompt. A bench module gives the machine:

    KEY, LABEL       where the record sits in the `hands` block, and what to call the bench
    SUBMISSION       the workspace file the sandbox runner hands back when a run changed it
    NOTE             what the agent is told about the task and the scoring
    PLAN_READS, PLAN_RULE, SYNTHESIS_RULE   what the planning and closing briefs gain from it
    score(state, text)        -> (score, bench version) or (None, why not)
    record(state, db)         -> every scored run, the best, and the host facts
    report(score, record, run) -> (what the next turn is told, the line the host posts)
"""
import json

KEY = "openrsi_bench"
LABEL = "OpenRSI"
SUBMISSION = "openrsi/submission.json"
NOTE = ("THE OPENRSI BENCH, for OpenRSI work. data/openrsi/ (read-only) holds a classification task "
        "from a hidden generator: 24 numbers in, one of 6 classes out. labelled_x.npy (120 x 24) with "
        "labelled_y.npy (120 labels, 20 per class) are the only labels there are. unlabelled_x.npy "
        "(6000 x 24) has none. eval_x.npy (2000 x 24) is held out: its labels are kept by the host and "
        "exist nowhere in the workspace. To be scored, a run writes openrsi/submission.json = "
        "{\"predictions\": [2000 integers 0-5, in eval_x order]}. When the command exits 0 the host "
        "scores it and posts held-out accuracy itself. THAT IS THE ONLY SCORE: it arrives as "
        "`last_run.host_score`, and `scores` here is the whole record. Chance is 16.7%. Anything a "
        "script prints about its own accuracy is a training-side number and must be labelled as one. "
        "The question OpenRSI asks here: starting from 120 labels, can a small model improve by "
        "training on its own outputs for the unlabelled data, with no stronger teacher? Start with an "
        "honest supervised baseline. After that, exactly one change to the training per run; keep what "
        "the host's score says improved, drop what did not, and say which. Save model state to disk "
        "(np.savez) so a later run continues real work. THREE HOST FACTS sit beside the scores. "
        "`noise_floor`: one standard error of the score; a gap smaller than it is not a finding and is "
        "never called a gain. `runs_since_gain_above_noise`: scored runs since the score last rose by "
        "more than that. `reference`: what plain methods score on the same held-out set from the 120 "
        "labels alone, measured by the host; a method scoring below a reference is weaker than that "
        "plain method, however carefully it was tuned.")
PLAN_READS = "`hands.openrsi_bench.scores`, "
PLAN_RULE = ("For bench work: when `hands.openrsi_bench.runs_since_gain_above_noise` is 4 or more, or "
             "the best score is below a `reference`, the method is the limit. Then the sprint changes "
             "the method (the model family, the features, or how the unlabelled data is used) and "
             "spends no step on re-running, re-seeding, hashing or re-tuning the method that stalled. ")
SYNTHESIS_RULE = (" For bench work, a gap under `noise_floor` is reported as no change; and when "
                  "`runs_since_gain_above_noise` is 4 or more, `next_order` names a different method, "
                  "never another check or setting of the same one.")


def targets(state):
    return state / "openrsi_targets.json"


def score(state, text):
    """Held-out accuracy of a run's predictions, or (None, why not).

    The labels are on the host side only. The sandbox cannot read them and no prompt carries them,
    so a script cannot produce this number by any means except predicting well.
    """
    try:
        bench = json.loads(targets(state).read_text(encoding="utf-8"))
        labels, classes = bench["labels"], bench["classes"]
    except (OSError, ValueError, KeyError):
        return None, "no bench is installed"
    try:
        predictions = json.loads(text)["predictions"]
        assert isinstance(predictions, list)
    except Exception:
        return None, "submission is not JSON with a `predictions` list"
    if len(predictions) != len(labels):
        return None, f"expected {len(labels)} predictions, got {len(predictions)}"
    clean = []
    for p in predictions:
        if isinstance(p, float) and p.is_integer():
            p = int(p)
        if isinstance(p, bool) or not isinstance(p, int) or not 0 <= p < classes:
            return None, f"every prediction must be an integer 0-{classes - 1}"
        clean.append(p)
    return sum(p == t for p, t in zip(clean, labels)) / len(labels), bench.get("version", "")


def record(state, db):
    """Every host-scored run on the current bench, oldest first, and the best of them.

    Plus what the host can work out and the agent cannot: how big a gap has to be before it means anything,
    how long since one appeared, and what plain methods score on the same held-out set. Without these
    it spent a night confirming a one-example gain (0.5345 to 0.535 of 2000) as if it were a result.
    """
    try:
        bench = json.loads(targets(state).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    runs = [{"run": run, "held_out_accuracy": round(score, 4)} for run, score in db.execute(
        "select id, score from jobs where score is not null and score_note=? order by id",
        (bench.get("version", ""),))]
    best = max(runs, key=lambda item: item["held_out_accuracy"]) if runs else None
    record = {"scored_runs": len(runs), "best": best, "scores": runs[-12:]}
    if runs:
        level, n = best["held_out_accuracy"], len(bench.get("labels") or []) or 2000
        floor = round(max(0.005, (level * (1 - level) / n) ** 0.5), 4)  # one standard error of the score
        mark, since = None, 0
        for item in runs:
            if mark is None or item["held_out_accuracy"] > mark + floor:
                mark, since = item["held_out_accuracy"], 0
            else:
                since += 1
        record.update(noise_floor=floor, runs_since_gain_above_noise=since)
    if bench.get("reference"):
        record["reference"] = bench["reference"]
    return record


def report(score, record, run):
    best = record.get("best") or {}
    told = {"run": run, "held_out_accuracy": round(score, 4),
            "best_so_far": best.get("held_out_accuracy"), "best_run": best.get("run")}
    line = f"held-out accuracy {score:.2%}"
    if best and best.get("run") != run:
        line += f" · best {best['held_out_accuracy']:.2%} (run {best['run']})"
    plain = (record.get("reference") or {}).get("nearest_neighbour_k1")
    if plain and (best.get("held_out_accuracy") or 0) < plain:  # shown until the agent has beaten it
        line += f" · plain nearest-neighbour {plain:.2%}"
    return told, line + " · scored by the host"
