"""Fits elo.CALIBRATION on one player's plays of the last 90 days: for each skillset, a logistic regression of passing a
challenge on log(difficulty) gives how many rating points one e-fold of difficulty is worth (400 points = 10 times the
odds, as in Elo), and the difficulty this player passes at their own pass rate becomes 1200. The result is printed, to
be pasted into elo.py: the scales stay fixed after that, with this player as the reference (1200 in every skillset).

    .venv\\Scripts\\python tools\\calibrate-elo.py
"""

import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from osu_coach import elo, gui  # noqa: E402


def fit(bins) -> tuple[float, float] | None:
    """(a, b) of logit(pass) = a - b * log(difficulty), weighted by the challenges in each bin."""
    x = np.array([b[0] for b in bins])
    w = np.array([b[1] for b in bins], dtype=float)
    l = np.array([b[2] for b in bins], dtype=float)
    n = w + l
    if n.sum() < 200 or l.sum() < 10 or w.sum() < 10:
        return None
    A = np.column_stack([np.ones_like(x), -x])
    beta = np.array([math.log(w.sum() / l.sum()), 0.0])
    for _ in range(100):
        p = 1 / (1 + np.exp(-(A @ beta)))
        grad = A.T @ (w - n * p) - 1e-6 * beta
        hess = (A * (n * p * (1 - p))[:, None]).T @ A + 1e-6 * np.eye(2)
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.abs(step).max() < 1e-10:
            break
    return float(beta[0]), float(beta[1])


def median_log(bins) -> float:
    total, seen = sum(w + l for _, w, l in bins), 0
    for log_d, w, l in sorted(bins):
        seen += w + l
        if seen >= total / 2:
            return log_d
    return 0.0


if __name__ == "__main__":
    settings = gui.load_settings()
    job = gui.Job("calibrate")
    plays = [p for p in gui.elo_plays(job, settings) if p["time"] >= time.time() - elo.DAYS * 86400]
    print(f"{len(plays)} plays of the last {elo.DAYS} days")
    out, rates = {}, {}
    for skill, (label, _, _) in elo.SKILLS.items():
        bins = [b for p in plays for b in p["skills"].get(skill, [])]
        n = sum(w + l for _, w, l in bins)
        # 1200 = passing as often as the reference player does: their own pass rate on these challenges
        target = min(max(sum(w for _, w, _ in bins) / max(n, 1), 0.5), 0.995)
        rates[skill] = round(target, 3)
        ab = fit(bins)
        if ab is None or ab[1] <= 0:
            # no clear effect of the difficulty: the median challenge is the reference, 400 points per e-fold
            d0 = math.exp(median_log(bins))
            out[skill] = (d0, 400.0)
            print(f"  {label}: {n} challenges, {target:.1%} passed, no clear effect of the difficulty: median {d0:.4g}")
            continue
        a, b = ab
        per_e = b * 400 / math.log(10)
        d0 = math.exp((a - math.log(target / (1 - target))) / b)
        out[skill] = (d0, per_e)
        print(f"  {label}: {n} challenges, {target:.1%} passed; that rate at difficulty {d0:.4g}; {per_e:.0f} points per e-fold")
    print()
    print("pass rates (the second value of each SKILLS entry):", rates)
    print()
    print("CALIBRATION = {")
    for skill, (d0, per_e) in out.items():
        print(f'    "{skill}": ({d0:.6g}, {per_e:.1f}),')
    print("}")
