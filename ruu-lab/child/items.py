"""Item sets for the child system: generated logic tasks whose answers stay on uk.

Tasks come from reasoning-gym's seeded generators. The GPU machine is sent the questions only;
the seeds, and so the answers, never leave this host. An item is stored as (family, seed, index)
and rebuilt at scoring time, with a hash of the question to catch any drift in the generator.

`seen` families may be used to make training data in later stages. `held_out` families never are:
a gain there is the first result worth announcing (rung 2 of the plan).

    items.py build logic-v1          # write dev and sealed sets
    items.py show logic-v1           # counts
"""
import contextlib
import hashlib
import importlib.metadata
import json
import os
import sys
import warnings

from common import ITEMS

warnings.filterwarnings("ignore")

SEEN = ["basic_arithmetic", "chain_sum", "fraction_simplification", "gcd", "lcm", "prime_factorization",
        "base_conversion", "bitwise_arithmetic", "simple_equations", "products",
        "propositional_logic", "syllogism", "knights_knaves", "family_relationships", "circuit_logic",
        "number_sorting", "spell_backward", "letter_counting", "binary_matrix", "rotate_matrix",
        "string_manipulation", "count_primes", "mini_sudoku", "n_queens", "maze", "tower_of_hanoi",
        "count_bits", "bf", "gsm_symbolic", "leg_counting", "calendar_arithmetic", "time_intervals"]
# Not used, found 2026-11-20: codeio runs stored code to make each item, needs scipy, and gave a
# different item for the same seed on a second build. isomorphic_strings and polynomial_multiplication
# give different items in a fresh process (they depend on Python's per-process hash seed). An item
# that cannot be rebuilt cannot be scored here, so every family must pass the rebuild test first.
HELD_OUT = ["zebra_puzzles", "futoshiki", "shortest_path", "course_schedule", "cryptarithm", "number_sequence",
            "word_sorting", "complex_arithmetic", "game_of_life", "simple_integration", "self_reference",
            "color_cube_rotation"]
SETS = {"logic-v1": {"groups": {"seen": SEEN, "held_out": HELD_OUT}, "per_family": {"seen": 80, "held_out": 100},
                     "seeds": {"dev": 1000, "sealed": 2000, "pool": 3000}, "pool_per_family": 120},
        "code-v1": {"kind": "code", "splits": ["dev"]}}  # built by codesuite.py, not here
# `pool` is where training data comes from (stage 2): seen families only, seeds from 3000 up, and any
# question that also appears in dev or sealed is dropped from it. No test set may use those seeds.
SYSTEM = ("Given a problem, your task is to answer the question by thinking step-by-step in a clear and specific "
          "manner.\nOnce you have thought about the reasoning process, provide the answer in the following format:\n"
          "<answer>answer here</answer>\nDo not explain your reasoning inside the answer tags, provide only the "
          "final answer. When an example is provided, you should strictly follow the format of the output/answer "
          "in that example.\n")


# The same items can be asked in different ways. `default` is the generator's own prompt. The others
# exist because the first screen (2026-11-20) lost 30% of answers to the token cap, not to wrong answers.
SYSTEMS = {
    "default": SYSTEM,
    "concise": ("Solve the problem. Keep your working short: only the steps you need. The moment you have the "
                "result, stop and write it as <answer>answer here</answer>. Inside the tags put only the final "
                "answer, in the format the question asks for or shows in its example.\n"),
    "draft": ("Think step by step, but keep each step to a minimal draft of a few words. Then give the result as "
              "<answer>answer here</answer>, with only the final answer inside the tags, in the format the "
              "question asks for or shows in its example.\n"),
    "direct": ("Answer the question directly, with no explanation or working. Write the result as "
               "<answer>answer here</answer>, in the format the question asks for or shows in its example.\n"),
}


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@contextlib.contextmanager
def quiet():
    """Some generators print progress. Keep it out of our output, which other programs read as JSON."""
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
        yield


def dataset(family, size, seed):
    import reasoning_gym
    with quiet():
        return reasoning_gym.create_dataset(family, size=size, seed=seed)


def build(name):
    spec = SETS[name]
    out = ITEMS / name
    out.mkdir(parents=True, exist_ok=True)
    taken, manifest = set(), {"set": name, "generator": "reasoning-gym " + importlib.metadata.version("reasoning-gym"),
                              "groups": spec["groups"], "splits": {}}
    for split, base in spec["seeds"].items():  # dev first, then sealed, then pool: a clash is dropped from the later one
        rows, dropped = [], 0
        for group, families in spec["groups"].items():
            if split == "pool" and group != "seen":
                continue  # held-out families never supply training data
            for position, family in enumerate(sorted(families)):
                seed = base + (0 if group == "seen" else 500) + position
                size = spec["pool_per_family"] if split == "pool" else spec["per_family"][group]
                data = dataset(family, size, seed)
                with quiet():
                    questions = [data[index]["question"] for index in range(size)]
                for index, question in enumerate(questions):
                    key = (family, sha(question))
                    if key in taken:
                        dropped += 1
                        continue
                    taken.add(key)
                    rows.append({"id": f"{name}/{split}/{family}/{index}", "family": family, "group": group,
                                 "seed": seed, "size": size, "index": index, "sha": key[1], "question": question})
        path = out / f"{split}.jsonl"
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
        path.chmod(0o600)
        manifest["splits"][split] = {"items": len(rows), "duplicates_dropped": dropped,
                                     "seen": sum(r["group"] == "seen" for r in rows),
                                     "held_out": sum(r["group"] == "held_out" for r in rows)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def load(name, split):
    with (ITEMS / name / f"{split}.jsonl").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def splits(name):
    return list(SETS[name].get("seeds") or SETS[name]["splits"])


def prompts(rows, style="default"):
    """What the GPU machine is given: an id and the conversation. No seed, no family, no answer."""
    if rows and rows[0].get("kind") == "code":
        import codesuite
        return codesuite.prompts(rows)
    return [{"id": row["id"], "messages": [{"role": "system", "content": SYSTEMS[style]},
                                           {"role": "user", "content": row["question"]}]} for row in rows]


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "build" and "seeds" in SETS.get(sys.argv[2], {}):
        print(json.dumps(build(sys.argv[2])["splits"], indent=1))
    elif len(sys.argv) == 3 and sys.argv[1] == "show":
        print((ITEMS / sys.argv[2] / "manifest.json").read_text(encoding="utf-8"))
    else:
        sys.exit(__doc__)
