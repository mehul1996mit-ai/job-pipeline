"""Structured skill matcher — ported from cv-match-copilot's lib/scoring2.js.

Deliberately separate from the frozen engine in scoring_core.py. That stays
the headline score (and keeps its acceptance regression); this produces the
*structured* skill sub-score that aggregate.py consumes.

Layered matching, tightest first, recording the layer as a confidence signal:
  exact > alias(synonym) > stem > phrase(bigram)
Weighting: requirement TIER (must-have 3x vs preferred 1x), source
(demonstrated > declared-only, the latter discounted — anti-gaming), recency
decay (a skill last used years ago counts less), and depth of evidence (a
skill appearing in several bullets outweighs a bare keyword).
Anti-gaming: the declared-only discount above, plus verbatim-JD-mirror
detection (a CV parroting the JD's exact phrasing is a stuffing signal).

Semantic/embedding and taxonomy layers are DEFERRED — the free Gemini tier has
no embedding endpoint worth spending calls on. Noted, not silently skipped.
"""
from __future__ import annotations

import re

from scoring_core import token_parts

# Layer -> confidence. Exact surface is certain; a matched bigram is strong
# evidence of a real competency; stem/alias are progressively looser.
LAYER_CONF = {"exact": 1.0, "phrase": 0.9, "alias": 0.9, "stem": 0.82}
LAYER_RANK = {"exact": 4, "phrase": 3, "alias": 2, "stem": 1, "none": 0}

TIER_WEIGHT = {"must": 3, "preferred": 1, "unknown": 1.5}
SOURCE_WEIGHT = {"demonstrated": 1.0, "declared": 0.5, "none": 1.0}
RECENCY_PER_YEAR = 0.06        # decay per year since a skill was last used
RECENCY_FLOOR = 0.5
DEPTH_STEP = 0.1               # per extra evidencing bullet, capped at 1.0

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9+#./-]+")


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def raw_tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT_RE.sub(" ", str(text or "").lower()).split() if t]


def index_layers(text: str) -> dict:
    """Layered token sets + a canonical-bigram set for a body of text."""
    raw, syn, canon, bigram = set(), set(), set(), set()
    prev_canon = None
    for tok in raw_tokens(text):
        p = token_parts(tok)
        if not p:
            prev_canon = None
            continue
        raw.add(p["raw"])
        syn.add(p["syn"])
        canon.add(p["canon"])
        if prev_canon:
            bigram.add(prev_canon + " " + p["canon"])
        prev_canon = p["canon"]
    return {"raw": raw, "syn": syn, "canon": canon, "bigram": bigram}


def token_layer(p: dict, cv: dict) -> str:
    """Tightest layer at which a single skill token hits the CV index."""
    if p["raw"] in cv["raw"]:
        return "exact"
    if p["syn"] in cv["syn"]:
        return "alias"
    if p["canon"] in cv["canon"]:
        return "stem"
    return "none"


def match_skill(skill: str, cv: dict, cv_lower: str) -> dict:
    """Match a whole skill phrase. A phrase matches when every content token
    hits; its layer is the WEAKEST token layer (a chain is only as strong as
    its loosest link), except a canonical-bigram hit upgrades a multiword skill
    to the 'phrase' layer, and a whole-phrase substring is strongest of all."""
    parts = [p for p in (token_parts(t) for t in raw_tokens(skill)) if p]
    if not parts:
        return {"layer": "exact" if str(skill).lower() in cv_lower else "none"}

    weakest = "exact"
    for p in parts:
        lyr = token_layer(p, cv)
        if lyr == "none":
            weakest = "none"
            break
        if LAYER_RANK[lyr] < LAYER_RANK[weakest]:
            weakest = lyr

    # Phrase-level confirmation for multiword skills.
    if len(parts) >= 2:
        for j in range(1, len(parts)):
            if parts[j - 1]["canon"] + " " + parts[j]["canon"] in cv["bigram"]:
                if weakest == "none" or LAYER_RANK["phrase"] > LAYER_RANK[weakest]:
                    weakest = "phrase"
                break

    if str(skill).lower() in cv_lower:
        weakest = "exact"
    return {"layer": weakest}


def skill_source(skill: str, parsed_cv: dict) -> dict:
    """How the skill is evidenced: demonstrated (in an experience bullet) beats
    declared-only (skills list, no supporting bullet)."""
    kl = str(skill).lower()
    dem = (parsed_cv.get("skills") or {}).get("demonstrated") or []
    for d in dem:
        if d["skill"].lower() == kl:
            return {"source": "demonstrated", "count": d["count"],
                    "recent_end": d.get("recent_end")}

    # Also treat a direct bullet substring hit as demonstrated — the skill may
    # be a JD term that isn't in the CV's own declared list.
    exp = parsed_cv.get("experience") or []
    count, recent_end = 0, None
    for role in exp:
        if any(kl in str(b).lower() for b in (role.get("bullets") or [])):
            count += 1
            if recent_end is None or role["end"] > recent_end:
                recent_end = role["end"]
    if count:
        return {"source": "demonstrated", "count": count, "recent_end": recent_end}

    decl = (parsed_cv.get("skills") or {}).get("declared") or []
    if any(str(d).lower() == kl for d in decl):
        return {"source": "declared"}
    return {"source": "none"}


def recency_factor(recent_end, now) -> float:
    if recent_end is None or not now:
        return 1.0
    years = max(0, (now - recent_end) / 12)
    return clamp(1 - years * RECENCY_PER_YEAR, RECENCY_FLOOR, 1)


def verbatim_mirror(jd_text: str, cv_text: str) -> dict:
    """Fraction of the CV's distinct trigrams appearing verbatim in the JD. A
    high value on a substantial CV suggests the resume was stuffed with the
    posting's exact phrasing rather than reflecting real history."""
    def trigrams(t):
        w = raw_tokens(t)
        return {" ".join(w[i - 2:i + 1]) for i in range(2, len(w))}

    jd, cv = trigrams(jd_text), trigrams(cv_text)
    if len(cv) < 20:
        return {"ratio": 0, "flagged": False, "sampled": len(cv)}
    hit = len(cv & jd)
    ratio = hit / len(cv)
    return {"ratio": round(ratio * 100) / 100, "flagged": ratio > 0.15,
            "sampled": len(cv)}


def tier_of(skill: str, must_set: set, pref_set: set) -> str:
    k = str(skill).lower()
    if k in must_set:
        return "must"
    if k in pref_set:
        return "preferred"
    return "unknown"


def structured_skill_match(jd: dict, parsed_cv: dict) -> dict:
    """Structured skill match: layered, weighted, evidence-aware.

    jd:        analyst output (key_skills / must_have_skills / preferred_skills)
    parsed_cv: parse_cv_structured output
    """
    jd = jd or {}
    parsed_cv = parsed_cv or {}
    sections = parsed_cv.get("sections") or {}
    cv_text = "\n".join(sections.get(k, "") for k in sections)
    cv = index_layers(cv_text)
    cv_lower = cv_text.lower()
    now = parsed_cv.get("now")

    must_set = {str(s).lower() for s in (jd.get("must_have_skills") or [])}
    pref_set = {str(s).lower() for s in (jd.get("preferred_skills") or [])}

    # Universe of skills to score: key ∪ must ∪ preferred, deduped.
    universe, seen = [], set()
    for s in ((jd.get("key_skills") or []) + (jd.get("must_have_skills") or [])
              + (jd.get("preferred_skills") or [])):
        k = str(s or "").strip()
        if not k:
            continue
        kl = k.lower()
        if kl in seen:
            continue
        seen.add(kl)
        universe.append(k)

    matched, missing = [], []
    total_weight = hit_weight = 0.0
    must_total = must_hit = pref_total = pref_hit = 0

    for skill in universe:
        tier = tier_of(skill, must_set, pref_set)
        tw = TIER_WEIGHT[tier]
        total_weight += tw
        if tier == "must":
            must_total += 1
        elif tier == "preferred":
            pref_total += 1

        mm = match_skill(skill, cv, cv_lower)
        if mm["layer"] == "none":
            missing.append({"skill": skill, "tier": tier})
            continue

        src = skill_source(skill, parsed_cv)
        conf = LAYER_CONF.get(mm["layer"], 0.8)
        sw = SOURCE_WEIGHT[src["source"]]
        rec = (recency_factor(src.get("recent_end"), now)
               if src["source"] == "demonstrated" else 1.0)
        depth = (clamp(1 + (max(1, src.get("count", 1)) - 1) * DEPTH_STEP,
                       1, 1 + 5 * DEPTH_STEP)
                 if src["source"] == "demonstrated" else 1.0)
        # Multiplier capped at 1 so a skill never exceeds its tier ceiling
        # (anti-domination); depth only offsets confidence/recency/source loss.
        mult = clamp(conf * sw * rec * depth, 0, 1)
        hit_weight += tw * mult
        if tier == "must":
            must_hit += 1
        elif tier == "preferred":
            pref_hit += 1

        matched.append({
            "skill": skill, "tier": tier, "layer": mm["layer"],
            "source": src["source"], "confidence": conf,
            "recency": round(rec * 100) / 100, "depth": src.get("count", 0),
            "weight": round(tw * mult * 100) / 100,
        })

    matched.sort(key=lambda m: -m["weight"])
    missing.sort(key=lambda m: 0 if m["tier"] == "must" else 1)

    return {
        "skill_score": (hit_weight / total_weight) if total_weight > 0 else 0,
        "matched": matched,
        "missing": missing,
        "must_coverage": {"hit": must_hit, "total": must_total},
        "preferred_coverage": {"hit": pref_hit, "total": pref_total},
        "verbatim_mirror": verbatim_mirror(jd.get("jd_text") or "", cv_text),
        "total_weight": round(total_weight * 100) / 100,
        "hit_weight": round(hit_weight * 100) / 100,
    }
