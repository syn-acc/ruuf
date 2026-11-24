"""Score a run on uk. The GPU machine returns text; every number is computed here.

    score.py ingest RUN_ID            # check each output against the regenerated task, store per-item results
    score.py show RUN_ID              # accuracy by group and family, with intervals
    score.py compare PARENT CHILD     # paired difference on shared items
"""
import json
import re
import sys
import warnings

import codesuite
import items
import stats
from common import RUNS, answer_region, open_db

warnings.filterwarnings("ignore")
ANSWER = re.compile(r"<answer>\s?(.*?)\s?</answer>", re.S)


def extract(text, thinking=False):
    """The last <answer> block outside any thinking. None when the model never committed to one."""
    found = ANSWER.findall(answer_region(text, thinking))
    return found[-1].strip() if found else None


def ingest(run_id):
    db = open_db()
    run = db.execute("select * from runs where run_id=?", (run_id,)).fetchone()
    if not run:
        raise SystemExit(f"no run {run_id}")
    job = RUNS / run_id / "job"
    rows = {row["id"]: row for row in items.load(run["item_set"], run["split"])}
    outputs = [json.loads(line) for line in (job / "outputs.jsonl").read_text(encoding="utf-8").splitlines() if line]
    sent = {json.loads(line)["id"] for line in (job / "prompts.jsonl").read_text(encoding="utf-8").splitlines() if line}
    datasets, drift, stored = {}, 0, 0
    thinking = bool(json.loads(run["spec"] or "{}").get("thinking"))
    code_problems = codesuite.problems() if any(r.get("kind") == "code" for r in rows.values()) else {}
    db.execute("delete from results where run_id=?", (run_id,))
    for out in outputs:
        row = rows.get(out["id"])
        if row is None or out["id"] not in sent:
            continue  # an id we never sent is not a result
        if row.get("kind") == "code":  # run in the scoring box, compared out here
            score, correct, note = codesuite.judge(code_problems[row["id"]], out.get("text"), thinking)
            db.execute("insert or replace into results values(?,?,?,?,?,?,?,?,?,?)",
                       (run_id, out["id"], row["family"], row["group"], int(out.get("sample", 0)), score,
                        int(correct), int(note != "load error"), int(out.get("tokens") or 0), out.get("finish")))
            stored += 1
            continue
        key = (row["family"], row["size"], row["seed"])
        if key not in datasets:
            datasets[key] = items.dataset(*key)
        with items.quiet():
            entry = datasets[key][row["index"]]
        if items.sha(entry["question"]) != row["sha"]:
            drift += 1  # the generator no longer makes the item we asked about: do not score it
            continue
        answer = extract(out.get("text"), thinking)
        try:
            with items.quiet():
                score = float(datasets[key].score_answer(answer=answer, entry=entry)) if answer is not None else 0.0
        except Exception:  # a scorer that chokes on a malformed answer has seen a wrong answer
            score = 0.0
        db.execute("insert or replace into results values(?,?,?,?,?,?,?,?,?,?)",
                   (run_id, out["id"], row["family"], row["group"], int(out.get("sample", 0)), score,
                    int(score >= 1.0), int(answer is not None), int(out.get("tokens") or 0), out.get("finish")))
        stored += 1
    meta_path = job / "meta.json"
    meta = meta_path.read_text(encoding="utf-8") if meta_path.exists() else None
    answered = db.execute("select count(distinct item_id) from results where run_id=?", (run_id,)).fetchone()[0]
    status = "scored" if answered == len(sent) else "partial"
    db.execute("update runs set status=?, meta=coalesce(?, meta) where run_id=?", (status, meta, run_id))
    db.commit()
    return {"run": run_id, "outputs": len(outputs), "stored": stored, "items_sent": len(sent),
            "items_answered": answered, "generator_drift": drift, "status": status}


def per_item(db, run_id):
    """One score per item: the mean over its samples, so k attempts read as expected single-attempt accuracy."""
    return {r["item_id"]: r for r in db.execute(
        "select item_id, family, grp, avg(score) score, avg(correct) correct, min(answered) answered, "
        "sum(finish='length') cut, avg(tokens) tokens from results where run_id=? group by item_id", (run_id,))}


def show(run_id):
    db = open_db()
    run = db.execute("select * from runs where run_id=?", (run_id,)).fetchone()
    got = per_item(db, run_id)
    if not run or not got:
        raise SystemExit(f"nothing scored for {run_id}")
    spec = json.loads(run["spec"] or "{}")
    how = f"{spec.get('style', 'default')}, thinking {'on' if spec.get('thinking') else 'off'}, cap {spec.get('max_new_tokens')}"
    if spec.get("tuned"):
        how += f" · child {spec['tuned']}"
    lines = [f"run {run_id} · {run['model']} · {run['item_set']}/{run['split']} · {how} · {run['status']}"]
    for group in sorted({r["grp"] for r in got.values()}) + ["all"]:
        rows = [r for r in got.values() if group == "all" or r["grp"] == group]
        k, n = sum(r["correct"] for r in rows), len(rows)  # with several samples: expected single-attempt accuracy
        low, high = stats.wilson(k, n)
        lines.append(f"  {group:9} {stats.pct(k / n):>5}%  [{stats.pct(low)}, {stats.pct(high)}]  n={n}  "
                     f"no answer {sum(not r['answered'] for r in rows)}  cut off {sum(bool(r['cut']) for r in rows)}  "
                     f"mean tokens {sum(r['tokens'] for r in rows) / n:.0f}")
    families = sorted({(r["grp"], r["family"]) for r in got.values()})
    for group, family in families:
        rows = [r for r in got.values() if r["family"] == family]
        done = [r for r in rows if not r["cut"]]
        finished = f"{stats.pct(sum(r['correct'] for r in done) / len(done)):>5}%" if done else "    -"
        lines.append(f"    {group:9} {family:28} {stats.pct(sum(r['correct'] for r in rows) / len(rows)):>5}%  n={len(rows):<4} "
                     f"cut off {len(rows) - len(done):>3}  right when finished {finished}")
    return "\n".join(lines)


def compare(parent, child):
    db = open_db()
    a, b = per_item(db, parent), per_item(db, child)
    shared = sorted(set(a) & set(b))
    if not shared:
        raise SystemExit("the two runs share no items")
    lines = [f"{child} minus {parent} on {len(shared)} shared items"]
    for group in sorted({a[i]["grp"] for i in shared}) + ["all"]:
        ids = [i for i in shared if group == "all" or a[i]["grp"] == group]
        # Exact answers, the same count `show` prints. Until 2026-11-20 this line used the generators'
        # partial credit for the levels and exact answers for gained/lost, so it disagreed with `show`.
        r = stats.paired([a[i]["correct"] for i in ids], [b[i]["correct"] for i in ids], [a[i]["family"] for i in ids])
        partial = (sum(a[i]["score"] for i in ids) / len(ids), sum(b[i]["score"] for i in ids) / len(ids))
        family = (f"  by family [{stats.pct(r['ci_family'][0])}, {stats.pct(r['ci_family'][1])}]"
                  if "ci_family" in r else "")
        lines.append(f"  {group:9} {stats.pct(r['parent'])} -> {stats.pct(r['child'])}  diff {stats.pct(r['diff']):>5} "
                     f"[{stats.pct(r['ci'][0])}, {stats.pct(r['ci'][1])}]{family}  gained {r['gained']} lost {r['lost']} "
                     f"p={r['p']:.3g}  n={r['n']}  {'CLEAR OF ZERO' if r['clear'] else 'inside the noise'}  "
                     f"(could show {stats.pct(stats.detectable(r['n'], max(r['flip_rate'], 0.01)))} points)  "
                     f"with partial credit {stats.pct(partial[0])} -> {stats.pct(partial[1])}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "ingest":
        print(json.dumps(ingest(sys.argv[2])))
    elif len(sys.argv) == 3 and sys.argv[1] == "show":
        print(show(sys.argv[2]))
    elif len(sys.argv) == 4 and sys.argv[1] == "compare":
        print(compare(sys.argv[2], sys.argv[3]))
    else:
        sys.exit(__doc__)
