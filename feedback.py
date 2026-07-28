"""Match-quality feedback and the learning loop it feeds.

You label each queued job good / partial / no. Those labels accumulate across
daily queue CSVs, and once there is enough signal this module PROPOSES changes
to the search and scoring config — which title keywords are pulling in junk,
which sub-scores actually separate a good match from a bad one, which sources
are worth their slot.

TWO HARD RULES, carried over from the source repo's outcome loop:

  1. It NEVER auto-applies. Everything here returns a proposal you accept or
     ignore. Silently re-tuning a scorer under someone is how a tool starts
     lying to them about their own history — last month's 72 has to still mean
     what it meant last month.
  2. It NEVER concludes below the floors below. Under them it reports the
     shortfall honestly instead of inventing a trend from six data points.

Labels live in the daily CSV's `match_feedback` column, so they are stored
day-wise and committed with the queue like everything else.
"""
from __future__ import annotations

import csv
import glob
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

LABELS = ("good", "partial", "no")

# Below these, no conclusion is drawn. A proposal built on a handful of labels
# would just be noise wearing a confidence interval.
MIN_LABELS = 25
MIN_PER_CLASS = 5
# A single batch may nudge a weight by at most this much, relative. Prevents
# one unrepresentative week from rewriting the scorer.
MAX_WEIGHT_NUDGE = 0.25
# A title keyword needs at least this many labelled jobs before its hit rate
# means anything.
MIN_KEYWORD_SAMPLES = 6


def load_labelled(data_dir="data") -> list[dict]:
    """Every labelled row across every daily queue, newest file last."""
    rows = []
    for path in sorted(glob.glob(str(Path(data_dir) / "job_queue_*.csv"))):
        day = Path(path).stem.replace("job_queue_", "")
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    label = (r.get("match_feedback") or "").strip().lower()
                    if label in LABELS:
                        r["_day"] = day
                        rows.append(r)
        except OSError:
            continue
    return rows


def label_counts(rows: list[dict]) -> dict:
    counts = {k: 0 for k in LABELS}
    for r in rows:
        counts[(r.get("match_feedback") or "").strip().lower()] += 1
    return counts


def readiness(rows: list[dict]) -> dict:
    """Can anything be concluded yet? Reports the shortfall plainly if not."""
    counts = label_counts(rows)
    total = sum(counts.values())
    missing = [f"{k} ({counts[k]}/{MIN_PER_CLASS})"
               for k in LABELS if counts[k] < MIN_PER_CLASS]
    ready = total >= MIN_LABELS and not missing
    if ready:
        note = (f"{total} labelled jobs with at least {MIN_PER_CLASS} in each "
                f"class — enough to propose changes.")
    elif total < MIN_LABELS:
        note = (f"{total}/{MIN_LABELS} labelled jobs so far. Keep labelling; "
                f"nothing is concluded below that floor.")
    else:
        note = ("Enough labels overall, but these classes are still thin: "
                + ", ".join(missing) + ". A proposal needs examples of each.")
    return {"ready": ready, "total": total, "counts": counts, "note": note}


def _num(row: dict, key: str):
    try:
        v = row.get(key)
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _positive(label: str) -> int:
    """good = 1, no = 0. `partial` is deliberately EXCLUDED from correlation
    rather than mapped to 0.5 — it's the ambiguous middle, and forcing it onto
    a binary axis is exactly how a weak signal gets amplified into a confident
    wrong answer."""
    return 1 if label == "good" else 0


def point_biserial(values: list[float], outcomes: list[int]):
    """Correlation between a continuous feature and a binary outcome.
    Returns None when the feature has no variance or a class is empty."""
    pairs = [(v, o) for v, o in zip(values, outcomes) if v is not None]
    if len(pairs) < 4:
        return None
    xs = [p[0] for p in pairs]
    ones = [x for x, o in pairs if o == 1]
    zeros = [x for x, o in pairs if o == 0]
    if not ones or not zeros:
        return None
    try:
        sd = statistics.pstdev(xs)
    except statistics.StatisticsError:
        return None
    if sd == 0:
        return None
    n = len(pairs)
    p, q = len(ones) / n, len(zeros) / n
    return ((statistics.mean(ones) - statistics.mean(zeros)) / sd
            * math.sqrt(p * q))


def score_separation(rows: list[dict]) -> dict:
    """Does the current score actually tell your good matches from your bad
    ones? This validates the scorer itself before anything is tuned on top of
    it — if it doesn't separate, re-weighting sub-scores is rearranging
    deck chairs."""
    good = [_num(r, "score") for r in rows
            if (r.get("match_feedback") or "").lower() == "good"]
    bad = [_num(r, "score") for r in rows
           if (r.get("match_feedback") or "").lower() == "no"]
    good = [g for g in good if g is not None]
    bad = [b for b in bad if b is not None]
    if not good or not bad:
        return {"separates": None, "note": "Not enough labelled jobs in both "
                                           "classes to check separation."}
    gm, bm = statistics.mean(good), statistics.mean(bad)
    delta = round(gm - bm, 1)
    r = point_biserial([_num(x, "score") for x in rows],
                       [_positive((x.get("match_feedback") or "").lower())
                        for x in rows])
    return {
        "separates": delta > 5,
        "good_mean": round(gm, 1), "bad_mean": round(bm, 1), "delta": delta,
        "correlation": round(r, 2) if r is not None else None,
        "note": (f"Jobs you called 'good' average {round(gm, 1)} vs "
                 f"{round(bm, 1)} for 'no' — a {delta}-point gap."
                 + ("" if delta > 5 else
                    " That's a weak gap: the score is not currently "
                    "distinguishing your good matches, so treat weight "
                    "tuning below as low-confidence.")),
    }


def keyword_performance(rows: list[dict], title_keywords: list[str]) -> list[dict]:
    """Hit rate per configured title keyword. A keyword whose jobs you almost
    always reject is pulling the search in the wrong direction."""
    out = []
    for kw in (title_keywords or []):
        k = kw.lower()
        hits = [r for r in rows if k in (r.get("title") or "").lower()]
        if len(hits) < MIN_KEYWORD_SAMPLES:
            continue
        counts = label_counts(hits)
        n = sum(counts.values())
        good_rate = counts["good"] / n if n else 0
        no_rate = counts["no"] / n if n else 0
        out.append({
            "keyword": kw, "samples": n, "good": counts["good"],
            "partial": counts["partial"], "no": counts["no"],
            "good_rate": round(good_rate, 2), "no_rate": round(no_rate, 2),
        })
    out.sort(key=lambda x: x["good_rate"])
    return out


def source_performance(rows: list[dict]) -> list[dict]:
    by_source = defaultdict(list)
    for r in rows:
        by_source[(r.get("source") or "?")].append(r)
    out = []
    for src, rs in by_source.items():
        counts = label_counts(rs)
        n = sum(counts.values())
        out.append({"source": src, "samples": n,
                    "good_rate": round(counts["good"] / n, 2) if n else 0,
                    **counts})
    out.sort(key=lambda x: -x["good_rate"])
    return out


def _sub_scores(row: dict) -> dict:
    try:
        v = json.loads(row.get("sub_scores") or "{}")
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def propose_weights(rows: list[dict], current: dict) -> dict:
    """Nudge sub-score weights toward whatever actually correlated with a good
    match. Bounded, renormalized to the original sum, and returned as a
    PROPOSAL — never written to config by this function."""
    labels = [(r.get("match_feedback") or "").lower() for r in rows]
    outcomes = [_positive(l) for l in labels]
    subs = [_sub_scores(r) for r in rows]
    keys = list(current.keys())

    correlations, usable = {}, 0
    for k in keys:
        vals = [s.get(k) for s in subs]
        vals = [float(v) if isinstance(v, (int, float)) else None for v in vals]
        r = point_biserial(vals, outcomes)
        correlations[k] = round(r, 3) if r is not None else None
        if r is not None:
            usable += 1

    if usable < 2:
        return {"proposed": None, "correlations": correlations,
                "note": "Not enough sub-score variation in the labelled jobs "
                        "to correlate anything. This usually means the queue "
                        "has been unusually uniform — keep labelling."}

    # Nudge each weight by its correlation, then hold TWO invariants at once:
    # no weight moves more than MAX_WEIGHT_NUDGE relative, and the set still
    # sums to what it summed to before. Renormalizing after clamping can push a
    # weight back past its cap, so alternate the two until they settle.
    strongest = max((abs(v) for v in correlations.values() if v is not None),
                    default=0)
    proposed = {}
    for k in keys:
        r = correlations.get(k)
        factor = 1.0 if not r or strongest == 0 else (
            1 + MAX_WEIGHT_NUDGE * (r / strongest))
        proposed[k] = current[k] * factor

    total_before = sum(current.values())
    lo = {k: current[k] * (1 - MAX_WEIGHT_NUDGE) for k in keys}
    hi = {k: current[k] * (1 + MAX_WEIGHT_NUDGE) for k in keys}
    for _ in range(20):
        total_after = sum(proposed.values())
        if total_after <= 0:
            break
        scaled = {k: v * total_before / total_after for k, v in proposed.items()}
        clamped = {k: min(hi[k], max(lo[k], v)) for k, v in scaled.items()}
        if all(abs(clamped[k] - proposed[k]) < 1e-12 for k in keys):
            proposed = clamped
            break
        proposed = clamped

    # Round for display, then put the rounding residual on the largest weight
    # so the reported numbers actually add up to the original total.
    proposed = {k: round(v, 4) for k, v in proposed.items()}
    residual = round(total_before - sum(proposed.values()), 4)
    if residual and keys:
        biggest = max(keys, key=lambda k: proposed[k])
        proposed[biggest] = round(proposed[biggest] + residual, 4)

    moved = sorted(((k, round(proposed[k] - current[k], 4)) for k in keys),
                   key=lambda kv: -abs(kv[1]))
    return {
        "proposed": proposed, "correlations": correlations,
        "biggest_moves": moved[:3],
        "note": ("Weights nudged toward the sub-scores that correlated with "
                 "your 'good' labels, capped at "
                 f"{int(MAX_WEIGHT_NUDGE * 100)}% relative movement per batch "
                 "and renormalized. Modelled from your labels, not measured "
                 "hiring outcomes."),
    }


def build_proposal(config: dict, data_dir="data") -> dict:
    """Everything the dashboard needs to show a review-and-accept panel."""
    rows = load_labelled(data_dir)
    ready = readiness(rows)
    result = {
        "readiness": ready,
        "separation": score_separation(rows),
        "keywords": keyword_performance(
            rows, (config.get("filters") or {}).get("title_keywords") or []),
        "sources": source_performance(rows),
        "weights": None,
        "suggested_drops": [],
    }
    if not ready["ready"]:
        return result

    current = ((config.get("scoring") or {}).get("weights")
               or {"skill_match": 0.40, "experience_fit": 0.20, "domain": 0.15,
                   "education": 0.10, "achievement": 0.10, "trajectory": 0.05})
    result["weights"] = propose_weights(rows, current)
    # A keyword you reject 70%+ of the time, with enough samples to trust it.
    result["suggested_drops"] = [
        k["keyword"] for k in result["keywords"]
        if k["no_rate"] >= 0.7 and k["samples"] >= MIN_KEYWORD_SAMPLES]
    return result
