"""Intervals and paired comparisons for evaluation results.

The rule this file exists to enforce: a difference between two models is only a result when it is
measured on the same items, has an interval that clears zero, and the interval is shown.
"""
import math

import numpy as np

Z = 1.959964  # 95%


def wilson(k, n):
    """95% interval for a proportion. Holds up at small n and near 0 or 1, where the usual +/- does not."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (p + Z * Z / (2 * n)) / (1 + Z * Z / n)
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / (1 + Z * Z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(gained, lost):
    """Two-sided exact test on the items that flipped. Items both models got the same carry no information."""
    n = gained + lost
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(gained, lost) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _percentiles(values):
    low, high = np.percentile(values, [2.5, 97.5])
    return float(low), float(high)


def paired(parent, child, clusters=None, draws=10000, seed=0):
    """Child minus parent on the same items.

    parent, child: per-item scores in [0, 1], same order. clusters: a label per item (the task family);
    when given, a second interval resamples whole families, which is the honest one for a claim about
    kinds of task rather than about these items.
    """
    a, b = np.asarray(parent, float), np.asarray(child, float)
    if a.shape != b.shape or a.size == 0:
        raise ValueError("paired() needs two equal, non-empty score lists")
    d, n, rng = b - a, a.size, np.random.default_rng(seed)
    means = []
    for start in range(0, draws, 500):  # in slices, so 10,000 draws over thousands of items stays small
        take = min(500, draws - start)
        means.append(d[rng.integers(0, n, (take, n))].mean(axis=1))
    right_a, right_b = a >= 1.0, b >= 1.0
    gained, lost = int((right_b & ~right_a).sum()), int((right_a & ~right_b).sum())
    out = {"n": int(n), "parent": float(a.mean()), "child": float(b.mean()), "diff": float(d.mean()),
           "ci": _percentiles(np.concatenate(means)), "gained": gained, "lost": lost,
           "flip_rate": (gained + lost) / n, "p": mcnemar_exact(gained, lost)}
    if clusters is not None:
        labels = np.asarray(clusters)
        names = sorted(set(labels.tolist()))
        if len(names) >= 5:
            sums = np.array([d[labels == name].sum() for name in names])
            sizes = np.array([(labels == name).sum() for name in names], float)
            pick = rng.integers(0, len(names), (draws, len(names)))
            out["ci_family"] = _percentiles(sums[pick].sum(axis=1) / sizes[pick].sum(axis=1))
            out["families"] = len(names)
    out["clear"] = bool(out["ci"][0] > 0 or out["ci"][1] < 0)
    return out


def detectable(n, flip_rate, power_z=0.8416):
    """Smallest difference n paired items can show at 80% power, when `flip_rate` of items change."""
    return (Z + power_z) * math.sqrt(flip_rate / n) if n else float("inf")


def pct(x, digits=1):
    return f"{100 * x:.{digits}f}"
