"""JD requirement extraction — the analyst input the structured scoring layer
needs (must-have vs preferred skills, minimum years, education level,
mandatory eligibility gates).

The browser extension this logic came from calls an LLM per posting, because it
only ever looks at the one page you have open. This pipeline sees hundreds of
listings a day, so an LLM call per job would exhaust the free tier within
minutes. Two analysts therefore exist:

  analyze_jd(text)                — DETERMINISTIC, free, runs on every job.
                                    Noisier: it extracts candidate phrases
                                    lexically rather than understanding them.
  merge_llm_analysis(base, llm)   — folds a richer analysis returned by the
                                    tailoring call (which already happens for
                                    the top-N jobs) over the deterministic one.

Both emit the same shape, and every result carries `analyst` so downstream
consumers can tell how much to trust it. A deterministic analysis is never
presented as if a model had read the posting.
"""
from __future__ import annotations

import re

from scoring_core import STOPWORDS, SYNONYMS

# Tier cues. A line matching MUST_CUE contributes must-have skills; PREF_CUE
# contributes preferred. A line matching neither still contributes "key" skills
# at unknown tier, which the structured matcher weights between the two.
MUST_CUE = re.compile(
    r"\b(must[- ]have|must possess|required|requirement|essential|mandatory"
    r"|minimum qualification|basic qualification|we require|you will need"
    r"|you must|should have)\b", re.I)
PREF_CUE = re.compile(
    r"\b(preferred|nice[- ]to[- ]have|good to have|desirable|a plus|is a plus"
    r"|bonus|advantage|would be great|ideally)\b", re.I)

# "3-5 years", "3 to 5 yrs", "5+ years", "minimum 5 years"
_RANGE_RE = re.compile(
    r"(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
_PLUS_RE = re.compile(
    r"(?:minimum|min\.?|at least)?\s*(\d{1,2})\s*\+\s*(?:years?|yrs?)"
    r"|(?:minimum|min\.?|at least)\s+(\d{1,2})\s*(?:years?|yrs?)", re.I)

_EDUCATION_RE = re.compile(
    r"\b(ph\.?\s?d|doctorate|mba|master'?s?|m\.?tech|m\.?sc|bachelor'?s?"
    r"|b\.?tech|b\.?e\b|b\.?sc|diploma)\b", re.I)

# Gates a CV genuinely cannot answer. These are FLAGGED for human review by the
# aggregation, never auto-failed — see aggregate.py.
_GATE_PATTERNS = [
    (re.compile(r"\b(work (authoriz|authoris)ation|visa|right to work"
                r"|work permit|sponsorship)\b", re.I), "work authorization/visa"),
    (re.compile(r"\b(security clearance|background clearance)\b", re.I),
     "security clearance"),
    (re.compile(r"\b(licen[cs]e[ds]?|certified|certification required"
                r"|must be certified)\b", re.I), "licence/certification"),
    (re.compile(r"\b(relocat|must be based|onsite only|willing to travel)\b", re.I),
     "location/relocation requirement"),
]

# Bare action verbs are not competencies. Only dropped when a candidate phrase
# is exactly one of them — "product management" must survive.
_VERB_NOISE = {
    "build", "building", "built", "drive", "driving", "own", "owning", "manage",
    "managing", "lead", "leading", "collaborate", "collaborating", "partner",
    "partnering", "define", "defining", "develop", "developing", "deliver",
    "delivering", "execute", "executing", "create", "creating", "design",
    "designing", "run", "running", "handle", "handling", "provide", "providing",
    "conduct", "conducting", "perform", "performing", "maintain", "maintaining",
    "identify", "identifying", "monitor", "monitoring", "report", "reporting",
    "track", "tracking", "coordinate", "coordinating", "responsible", "able",
    "willing", "strong", "excellent", "proven", "solid", "demonstrated",
    "hands", "hands-on", "track record", "attention", "detail", "written",
    "verbal", "need", "needs", "want", "wants", "seeking", "years",
}

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9+#./-]+")
# Clause separators inside a requirement line. Deliberately does NOT include
# "/" — "Agile/Scrum" and "UI/UX" are single competencies, not two.
_CLAUSE_SPLIT_RE = re.compile(r"[,;:()\[\]]|\band\b|\bor\b|\bwith\b|\bincluding\b",
                              re.I)
_LINE_SPLIT_RE = re.compile(r"[\n\r]+|(?:[.;!?]\s+)|(?:\s+[•·▪◦‣]\s*)")

MAX_PER_TIER = 12


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in _LINE_SPLIT_RE.split(str(text or "")) if ln.strip()]


def _phrases(line: str) -> list[str]:
    """Candidate skill phrases from one line: maximal runs of content words,
    emitted whole when short and as 2-grams when long.

    Purely lexical — it cannot tell a competency from a stray noun phrase, which
    is exactly why the LLM analyst overrides it where one is available.
    """
    # Clause boundaries first. A JD lists competencies comma-separated
    # ("product management, stakeholder management, agile"), and running the
    # n-gram walk across those commas invents phrases spanning two unrelated
    # skills ("management agile"). Splitting here is what keeps the must-have
    # list recognisable to a human.
    out = []
    for clause in _CLAUSE_SPLIT_RE.split(line):
        out.extend(_phrases_in_clause(clause))
    return out


def _phrases_in_clause(clause: str) -> list[str]:
    words = [w for w in _WORD_SPLIT_RE.sub(" ", clause.lower()).split() if w]
    out, run = [], []

    def flush():
        if not run:
            return
        if len(run) <= 3:
            out.append(" ".join(run))
        else:
            for i in range(1, len(run)):
                out.append(run[i - 1] + " " + run[i])
        run.clear()

    for w in words:
        # Edge punctuation survives tokenization because "." and "-" are legal
        # token characters (node.js, a/b, well-known). Strip it before the
        # stopword test or "required." sails past a list containing
        # "required" and lands in the must-have list as a fake competency.
        w = w.strip(".-")
        keep = bool(w) and ((w in SYNONYMS)
                            or (len(w) >= 3 and w not in STOPWORDS
                                and not re.fullmatch(r"[0-9.]+", w)))
        if keep:
            run.append(w)
        else:
            flush()
    flush()

    cleaned = []
    for p in out:
        # Trailing sentence punctuation survives tokenization because "." is a
        # legal token character (node.js, a/b). Strip it only at the end.
        p = p.strip().rstrip(".-")
        if not p or p in _VERB_NOISE:
            continue
        if len(p) < 3:
            continue
        # A single word that's pure verb noise carries no competency signal.
        if " " not in p and p in _VERB_NOISE:
            continue
        cleaned.append(p)
    return cleaned


def _dedupe(seq) -> list[str]:
    seen, out = set(), []
    for s in seq:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def parse_min_years(jd_text: str):
    """Minimum years the JD demands, or None when it states no requirement.
    (matcher.py keeps its own band parser for the hard include/exclude filter;
    this one reports the single floor the scoring curve needs.)"""
    m = _RANGE_RE.search(jd_text or "")
    if m:
        return min(int(m.group(1)), int(m.group(2)))
    m = _PLUS_RE.search(jd_text or "")
    if m:
        return int(m.group(1) or m.group(2))
    return None


def parse_education_level(jd_text: str) -> str:
    """The HIGHEST degree the posting names. Naming a degree is not the same as
    requiring one — aggregate.py only arms the eligibility gate when the level
    is genuinely above what the CV shows."""
    found = [m.group(0).lower() for m in _EDUCATION_RE.finditer(jd_text or "")]
    if not found:
        return ""
    order = ["diploma", "b.", "bachelor", "m.", "master", "mba", "phd", "doctorate"]

    def rank(s):
        for i, o in enumerate(reversed(order)):
            if s.startswith(o):
                return len(order) - i
        return 0

    return max(found, key=rank)


def parse_gates(jd_text: str) -> list[str]:
    """Mandatory eligibility a CV cannot verify. Reported for human review."""
    gates = []
    for rx, label in _GATE_PATTERNS:
        if rx.search(jd_text or ""):
            gates.append(label)
    return gates


def analyze_jd(jd_text: str) -> dict:
    """Deterministic requirement extraction. Free, runs on every job, noisier
    than a model read — and labelled as such in the returned `analyst` field."""
    jd_text = str(jd_text or "")
    must, pref, key = [], [], []

    for line in _lines(jd_text):
        phrases = _phrases(line)
        if not phrases:
            continue
        if MUST_CUE.search(line):
            must.extend(phrases)
        elif PREF_CUE.search(line):
            pref.extend(phrases)
        else:
            key.extend(phrases)

    must = _dedupe(must)[:MAX_PER_TIER]
    pref = [p for p in _dedupe(pref) if p not in must][:MAX_PER_TIER]
    taken = set(must) | set(pref)
    key = [p for p in _dedupe(key) if p not in taken][:MAX_PER_TIER * 2]

    return {
        "must_have_skills": must,
        "preferred_skills": pref,
        "key_skills": key,
        "min_years": parse_min_years(jd_text),
        "education_level": parse_education_level(jd_text),
        "mandatory_eligibility": parse_gates(jd_text),
        "jd_text": jd_text,
        "analyst": "deterministic",
    }


# Keys the LLM may supply. Anything else it returns is ignored rather than
# trusted into the scoring path.
_LLM_KEYS = ("must_have_skills", "preferred_skills", "key_skills",
             "min_years", "education_level", "mandatory_eligibility")


def merge_llm_analysis(base: dict, llm: dict | None) -> dict:
    """Fold an LLM-produced analysis over the deterministic one.

    Only non-empty LLM values override — a model that returned nothing for a
    field must not blank out a requirement the regex layer genuinely found.
    The result is marked `analyst: "llm"` only when the model actually
    contributed something, so a silently-failed call never masquerades as a
    richer read than it was.
    """
    out = dict(base or {})
    if not isinstance(llm, dict):
        return out

    contributed = False
    for k in _LLM_KEYS:
        v = llm.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            v = [str(x).strip() for x in v if str(x).strip()]
            if not v:
                continue
        elif isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        out[k] = v
        contributed = True

    if contributed:
        out["analyst"] = "llm"
    return out
