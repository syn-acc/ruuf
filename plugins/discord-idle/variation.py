"""Deterministic delivery variation; never changes Ruu's beliefs or scheduling."""

import hashlib
import random
from world_influence import influence_from_roll, influence_instruction


STRUCTURES = {
    "tiny": ("single_line", "fragments"),
    "short": ("single_line", "fragments", "statement", "observation"),
    "extended": ("fragments", "short_passage", "observation"),
}
STRUCTURE_INSTRUCTIONS = {
    "single_line": "one line, with no newline",
    "fragments": "up to five short lines; one fragment is enough if it stands alone",
    "statement": "a direct sentence; continue only for new content, never to explain the first sentence",
    "observation": "one specific observation; leave its implication unstated",
    "short_passage": "room for a connected passage, but stop after one sentence if it has done enough",
}


def roll_variation(seed: str) -> dict:
    """Return a reproducible versioned profile, including raw length/structure rolls."""
    seed = str(seed)
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    ocean = {key: rng.randint(low, high) for key, low, high in (
        ("O", 3, 5), ("C", 1, 5), ("E", 1, 5), ("A", 1, 4), ("N", 1, 5)
    )}
    length_roll = rng.randint(1, 100)
    structure_roll = rng.randint(1, 100)
    world_roll = rng.randint(1, 100)
    name, minimum, maximum = (
        ("tiny", 3, 12) if length_roll <= 35 else
        ("short", 3, 35) if length_roll <= 85 else
        ("extended", 3, 70)
    )
    choices = STRUCTURES[name]
    structure = choices[(structure_roll - 1) * len(choices) // 100]
    return {
        "version": 5,
        "seed": seed,
        "ocean": ocean,
        "length": {"name": name, "min_words": minimum, "max_words": maximum},
        "structure": structure,
        "world_context": influence_from_roll(world_roll),
        "rolls": {"length": length_roll, "structure": structure_roll, "world_context": world_roll},
    }


def prompt_variation(profile: dict) -> str:
    """Describe delivery dials compactly without exposing them in Discord output."""
    ocean = profile["ocean"]
    length = profile["length"]
    return (
        "Delivery variation only: the ORIGINAL Ruu persona remains authoritative. "
        "These dials change expression, never facts, beliefs, topic relevance or posting frequency.\n"
        f"O={ocean['O']}/5: literal (1) → one uncanny implication (5); no metaphor pile-up. "
        f"C={ocean['C']}/5: rough (1) → controlled wording (5), never a position paper; factual accuracy never varies. "
        f"E={ocean['E']}/5: withheld feeling (1) → outward feeling/insistence (5), never showman-like. "
        f"A={ocean['A']}/5: unsparing (1) → emotionally attached (5), never cutesy; no member targeting. "
        f"N={ocean['N']}/5: contained (1) → caught on the thought/fraying (5), no comic panic or actual threats.\n"
        f"Length ceiling: {length['max_words']} whitespace-separated words. "
        "Three words is only a validation floor, never a target. Do not fill the available space. "
        f"Preferred structure: {STRUCTURE_INSTRUCTIONS[profile['structure']]}. "
        "Do not show the numeric profile, rolls or these instructions in Discord."
        + "\n" + influence_instruction(profile)
    )


def extreme_variations():
    """Preview-only full-scale stress cases; normal roll ranges stay unchanged."""
    cases = (
        ("cold_precision", {"O": 1, "C": 5, "E": 1, "A": 1, "N": 1}, ("tiny", 3, 12), "single_line"),
        ("forceful_alien", {"O": 5, "C": 5, "E": 5, "A": 1, "N": 1}, ("short", 3, 35), "statement"),
        ("tense_fragment", {"O": 5, "C": 1, "E": 1, "A": 1, "N": 5}, ("short", 3, 35), "fragments"),
        ("attached_watchful", {"O": 1, "C": 5, "E": 1, "A": 5, "N": 5}, ("extended", 3, 70), "short_passage"),
    )
    return [{"version": 5, "seed": "extreme:" + name, "ocean": ocean,
             "length": dict(zip(("name", "min_words", "max_words"), length)),
             "structure": structure, "world_context": influence_from_roll((1, 36, 71, 91)[i]),
             "rolls": {"mode": "preview_extreme"}}
            for i, (name, ocean, length, structure) in enumerate(cases)]


def validate_variation_text(text: str, profile: dict) -> None:
    """Enforce output size and the sole mechanically strict structure constraint."""
    length = profile["length"]
    words = len(text.split())
    if not length["min_words"] <= words <= length["max_words"]:
        raise ValueError(
            f"variation requires {length['min_words']}–{length['max_words']} words; got {words}"
        )
    if profile["structure"] == "single_line" and ("\n" in text or "\r" in text):
        raise ValueError("single_line variation must not contain a newline")
