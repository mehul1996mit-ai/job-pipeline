"""Frozen match-scoring engine — ported from cv-match-copilot's lib/scoring.js.

FORMULA: score 0-100 = base (0-80) + domain bonus (0-20).
  base  = round( sqrt(hitWeight / totalWeight) x 80 )
  bonus = +5 per domain keyword present in BOTH JD and CV (substring), cap 20

The bonus is additive only — NEVER a filter. This mirrors the JS engine
semantics exactly, including its acceptance regression (a credit-risk JD must
beat a marketing JD by >25 points with the reference CV, while the marketing
JD still scores nonzero). If you change the formula shape here, that
regression in smoke_test.py is the guard that should stop you.

Tokens are canonicalized before matching so real matches aren't lost to
surface differences: morphological stemming (modelling/modeling/models ->
model) and synonym folding (js -> javascript). Canonical forms are used for
MATCHING; the original surface word is kept for DISPLAY.
"""
from __future__ import annotations

import math
import re

# Standard English stopwords plus resume/JD noise words that carry no signal.
STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "yours", "our", "ours", "are",
    "was", "were", "will", "would", "should", "could", "can", "may", "might",
    "have", "has", "had", "this", "that", "these", "those", "from", "into",
    "onto", "about", "over", "under", "then", "than", "them", "they", "their",
    "there", "here", "where", "when", "what", "which", "who", "whom", "whose",
    "how", "why", "not", "but", "all", "any", "each", "few", "more", "most",
    "other", "some", "such", "only", "own", "same", "very", "just", "also",
    "per", "via", "etc", "e.g", "i.e", "well", "able", "being", "been",
    "its", "his", "her", "him", "she", "out", "off", "too", "one",
    "two", "three", "new", "get", "use", "using", "used", "within", "across",
    "including", "include", "includes", "included", "like", "make", "makes",
    "making", "ensure", "ensuring", "help", "helping", "support", "supporting",
    # resume/JD noise:
    "experience", "experienced", "experiences", "skill", "skills", "skilled",
    "required", "require", "requires", "requirement", "requirements",
    "requiring", "candidate", "candidates", "year", "years", "role", "roles",
    "team", "teams", "strong", "knowledge", "work", "works", "worked",
    "working", "ability", "abilities", "responsibilities", "responsibility",
    "responsible", "preferred", "prefer", "plus", "must", "good", "great",
    "excellent", "job", "jobs", "position", "positions", "opportunity",
    "opportunities", "company", "companies", "looking", "seeking", "join",
    "day", "days", "month", "months", "understanding", "familiarity",
    "familiar", "proficiency", "proficient", "background", "degree",
    "bachelor", "bachelors", "master", "masters", "related", "relevant",
    "field", "min", "minimum", "maximum",
}

# Surface form -> canonical token. Applied BEFORE the length/number filters so
# short abbreviations (js, ts, py, k8s) survive. Single-token canonicals only.
SYNONYMS = {
    "js": "javascript", "node.js": "node", "nodejs": "node", "node": "node",
    "ts": "typescript",
    "py": "python",
    "k8s": "kubernetes",
    "postgres": "postgresql", "psql": "postgresql", "postgresql": "postgresql",
    "js6": "javascript", "es6": "javascript",
    "gcp": "gcp", "aws": "aws",
    "c/c++": "c++", "cpp": "c++",
    "ci/cd": "cicd", "cicd": "cicd",
    "reactjs": "react", "react.js": "react",
    "vuejs": "vue", "vue.js": "vue",
    "ai": "ai", "nlp": "nlp",
}

# Lines that look like requirements/qualifications get double weight.
REQ_LINE_RE = re.compile(
    r"(require|must[- ]have|qualif|essential|skills?|competenc|proficien"
    r"|expertise|mandator)", re.I)

# Posting ADMIN/METADATA lines — location, pay band, notice period, posting
# provenance. Their words are real words but never competencies, so they are
# excluded from the MATCHED/MISSING chips (DISPLAY only — scoring inputs are
# deliberately untouched so the frozen formula keeps its exact behaviour).
# Without this, a real posting offered "navi mumbai"/"vashi navi" as skills
# the CV was missing. Ported from lib/scoring.js (fixed there 2026-07-29).
META_LINE_RE = re.compile(
    r"^\s*(job\s*)?(location|locations|address|city|venue|salary|ctc"
    r"|compensation|pay|package|stipend|budget|notice\s*period"
    r"|posted\s*(by|on)|apply|contact|email|phone|reference"
    r"|req(uisition)?\s*(id|no|code)|employment\s*type|shift|vacanc"
    r"|openings?|no\.?\s*of\s*positions?)\b", re.I)

# Domain bonus keywords — overridable from config. Additive only, NEVER a filter.
DEFAULT_DOMAIN_KEYWORDS = [
    "lending", "credit", "nbfc", "fintech", "loan", "bfsi", "digital banking",
]

PER_TERM_WEIGHT_CAP = 8
BASE_MAX = 80
BONUS_PER_KEYWORD = 5
BONUS_CAP = 20

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9+#./-]+")
_LINE_SPLIT_RE = re.compile(r"[\n\r]+|(?:[.;!?]\s+)")

# A "bigram" used to be any two SURVIVING tokens sitting next to each other in
# the stopword-FILTERED list — not the same thing as being adjacent in the
# text. Two artifacts followed: dropped-stopword collapse ("Primary
# responsibility of the role would be to define…" -> phantom "primary
# define") and punctuation collapse ("Product Strategy, Product development"
# -> "strategy product"). Neither is a competency any CV could match, and
# both inflated total_weight with unmatchable terms, depressing every score.
# Fixed (ported from lib/scoring.js, fixed there 2026-07-29) by splitting a
# line into SEGMENTS at list/clause punctuation and recording each token's
# RAW position so a bigram only forms from genuinely neighbouring words.
# "/" and "." stay word characters (so "agile/scrum" and "node.js" survive
# whole) and are deliberately NOT segment separators.
_SEGMENT_SPLIT_RE = re.compile(r"[,;:&()\[\]{}|\"'`•·*]+|\s[-–—]+\s|\s{3,}")


def js_round(x: float) -> int:
    """JS Math.round: halves go toward +infinity (Python's round() would use
    banker's rounding and silently shift scores by a point)."""
    return math.floor(x + 0.5)


def stem(w: str) -> str:
    """Light, consistency-first stemmer. Not linguistically perfect roots —
    only collapsing common inflections the SAME way on both sides so
    equivalent words share a key. Display always uses the original surface."""
    if len(w) <= 4:
        return w
    if "+" in w or "#" in w:
        return w

    # British -> American spelling (common in resumes/JDs)
    w = re.sub(r"isation$", "ization", w)
    w = re.sub(r"ising$", "izing", w)
    w = re.sub(r"ise$", "ize", w)
    w = re.sub(r"yse$", "yze", w)

    o = w
    if o.endswith("ing") and len(o) > 5:
        o = re.sub(r"ing$", "", o)
        o = re.sub(r"([bdglmnprt])\1$", r"\1", o)     # modelling -> model
    elif o.endswith("ied"):
        o = re.sub(r"ied$", "y", o)                    # studied -> study
    elif o.endswith("ed") and len(o) > 4:
        o = re.sub(r"ed$", "", o)
        o = re.sub(r"([bdglmnprt])\1$", r"\1", o)      # modelled -> model

    if o.endswith("ies") and len(o) > 4:
        o = re.sub(r"ies$", "y", o)                    # libraries -> library
    elif re.search(r"(ss|sh|ch|x|z)es$", o):
        o = re.sub(r"es$", "", o)                      # classes -> class
    elif re.search(r"[^s]s$", o) and len(o) > 4:
        o = re.sub(r"s$", "", o)                       # models -> model

    return o if len(o) >= 3 else w


def canonical_token(raw: str):
    """Raw surface token -> {"c": canonical, "s": surface} or None if dropped."""
    t = re.sub(r"^[./-]+", "", raw)
    t = re.sub(r"[./-]+$", "", t)
    if not t:
        return None
    mapped = SYNONYMS.get(t)
    if not mapped:
        if len(t) <= 2:
            return None
        if re.fullmatch(r"[0-9.]+", t):
            return None
        if t in STOPWORDS:
            return None
    return {"c": stem(mapped or t), "s": t}


def token_parts(raw: str):
    """Layered breakdown of a token so a consumer can tell HOW two tokens
    match (exact surface / alias-folded / stem-folded) — the confidence signal
    the structured matcher needs. Mirrors canonical_token's filters exactly."""
    t = str(raw or "").lower()
    t = re.sub(r"^[./-]+", "", t)
    t = re.sub(r"[./-]+$", "", t)
    if not t:
        return None
    mapped = SYNONYMS.get(t)
    if not mapped:
        if len(t) <= 2:
            return None
        if re.fullmatch(r"[0-9.]+", t):
            return None
        if t in STOPWORDS:
            return None
    syn = mapped or t
    return {"raw": t, "syn": syn, "canon": stem(syn)}


def segments_of(line: str) -> list[str]:
    return [s for s in _SEGMENT_SPLIT_RE.split(str(line or "")) if s.strip()]


def tokens_of_segment(segment: str) -> list[dict]:
    """Tokens of ONE segment, each carrying "i" — its index in the segment's
    raw word list — so callers can tell real adjacency from filter-induced
    adjacency. Returns {"c": canonical, "s": surface, "i": raw position}."""
    raw = _TOKEN_SPLIT_RE.sub(" ", str(segment or "").lower()).split()
    out = []
    for i, r in enumerate(raw):
        tk = canonical_token(r)
        if tk:
            out.append({"c": tk["c"], "s": tk["s"], "i": i})
    return out


def tokens_of(line: str) -> list[dict]:
    """All tokens of a line, in order, as one flat list. Bigrams are never
    formed across a segment boundary — each_adjacent_pair walks segments
    separately; this is for token-only consumers."""
    out = []
    for seg in segments_of(line):
        out.extend(tokens_of_segment(seg))
    return out


def tokenize_line(line: str) -> list[str]:
    """Public tokenizer: canonical tokens only."""
    return [t["c"] for t in tokens_of(line)]


def split_lines(text: str) -> list[str]:
    return [ln for ln in _LINE_SPLIT_RE.split(str(text or "")) if ln.strip()]


def each_adjacent_pair(line, cb) -> None:
    """Walk every genuinely-adjacent token pair of a line, segment by
    segment. cb(prev_token, token) fires only when the two words really did
    sit next to each other in the source text."""
    for seg in segments_of(line):
        toks = tokens_of_segment(seg)
        for j in range(1, len(toks)):
            if toks[j]["i"] == toks[j - 1]["i"] + 1:
                cb(toks[j - 1], toks[j])


def index_text(text: str) -> dict:
    """Canonical token set + adjacent-token bigram set for a body of text."""
    tokens: set[str] = set()
    bigrams: set[str] = set()
    for line in split_lines(text):
        for tk in tokens_of(line):
            tokens.add(tk["c"])

        def _add(a, b):
            bigrams.add(a["c"] + " " + b["c"])
        each_adjacent_pair(line, _add)
    return {"tokens": tokens, "bigrams": bigrams}


def compute_match(jd_text: str, cv_text: str, domain_keywords=None, cv_index=None) -> dict:
    """Score a job description against the user's FULL CV text.

    Returns score/base/bonus/coverage/matched/missing/bonus_keywords plus the
    raw weights, so callers can explain the number rather than assert it.

    `cv_index`: pass a pre-computed index_text(cv_text) result to skip
    re-tokenizing the same (constant, per-run) CV text on every call —
    matcher.score_job() runs up to 3x per job listing across a daily run,
    so main.py computes this once and threads it through. Falls back to
    computing it here when not supplied, so existing callers (tests, the
    dashboard) are unaffected.
    """
    cv = cv_index if cv_index is not None else index_text(cv_text)
    cv_lower = str(cv_text or "").lower()
    jd_lower = str(jd_text or "").lower()

    # canonical term -> record. Note: when a term recurs only its WEIGHT
    # accumulates — display/is_bigram/hit stay as first seen, matching the JS.
    terms: dict[str, dict] = {}

    def add_term(canon, display, weight, is_bigram, hit, is_content):
        rec = terms.get(canon)
        if rec is None:
            rec = {"term": canon, "display": display, "weight": 0,
                   "is_bigram": is_bigram, "hit": hit, "content_hits": 0}
            terms[canon] = rec
        rec["weight"] = min(PER_TERM_WEIGHT_CAP, rec["weight"] + weight)
        if is_content:
            rec["content_hits"] += 1

    for line in split_lines(jd_text):
        line_weight = 2 if REQ_LINE_RE.search(line) else 1
        # Posting METADATA (location, salary band, notice period, posted-by…)
        # still scores exactly as before — removing it from scoring would
        # change the frozen formula's inputs. But it must never reach the
        # CHIPS: telling a candidate their CV is "missing navi mumbai" is
        # nonsense. A term is chip-eligible only if it occurred at least once
        # on a real content line. Ported from lib/scoring.js (2026-07-29).
        is_content = not META_LINE_RE.match(line)
        for tk in tokens_of(line):
            add_term(tk["c"], tk["s"], 1 * line_weight, False,
                     tk["c"] in cv["tokens"], is_content)

        def _add_bigram(a, b):
            # A bigram is a competency of its own: it only matches the CV's
            # own bigram set. Having both words separately does NOT count.
            bc = a["c"] + " " + b["c"]
            bd = a["s"] + " " + b["s"]
            add_term(bc, bd, 2 * line_weight, True, bc in cv["bigrams"], is_content)
        each_adjacent_pair(line, _add_bigram)

    total_weight = sum(r["weight"] for r in terms.values())
    hit_weight = sum(r["weight"] for r in terms.values() if r["hit"])

    coverage = (hit_weight / total_weight) if total_weight > 0 else 0.0
    # sqrt: real JDs rarely exceed ~60% raw coverage, so linear scaling would
    # pin every honest score in the low band.
    base = js_round(math.sqrt(coverage) * BASE_MAX)

    keywords = domain_keywords if domain_keywords else DEFAULT_DOMAIN_KEYWORDS
    bonus_keywords = []
    for kw in keywords:
        k = str(kw).lower()
        if k and k in jd_lower and k in cv_lower:
            bonus_keywords.append(k)
    bonus = min(BONUS_CAP, len(bonus_keywords) * BONUS_PER_KEYWORD)

    matched_terms = [r for r in terms.values() if r["hit"]]
    missing_terms = [r for r in terms.values() if not r["hit"]]

    def rank_key(rec):
        return (0 if rec["is_bigram"] else 1, -rec["weight"])

    matched_terms.sort(key=rank_key)
    missing_terms.sort(key=rank_key)

    def display(lst):
        # Chip-eligible only if it occurred on a real (non-metadata) content
        # line at least once — see the META_LINE_RE note above.
        visible = [r for r in lst if r["content_hits"] > 0]
        # Drop a unigram when a bigram ALREADY SHOWN IN THE SAME LIST contains
        # it ("product management" shouldn't also show "product"/"management").
        # Computed PER LIST, not globally: a matched unigram must never be
        # hidden by an unrelated MISSING bigram that merely shares a word
        # (e.g. matched "kubernetes" hidden by missing "kubernetes essential").
        # Ported from lib/scoring.js (fixed there 2026-07-29).
        halves: set[str] = set()
        for r in visible:
            if r["is_bigram"]:
                halves.update(r["term"].split(" "))
        return [r["display"] for r in visible
                if r["is_bigram"] or r["term"] not in halves]

    return {
        "score": max(0, min(100, base + bonus)),
        "base": base,
        "bonus": bonus,
        "coverage": coverage,
        "matched": display(matched_terms),
        "missing": display(missing_terms),
        "bonus_keywords": bonus_keywords,
        "total_weight": total_weight,
        "hit_weight": hit_weight,
    }


def skill_coverage(skills, cv_text: str) -> dict:
    """Skill-level coverage of a CLEAN skill list against the CV.

    The antidote to noisy matched/missing chips: compute_match scores raw JD
    bigrams, so a generically-scraped JD fills the lists with page boilerplate.
    This takes curated skill phrases and reports, per skill, whether the CV
    evidences it.

    Deliberately lenient: a false "covered" is a missed tailoring opportunity,
    but a false "missing" would push the tailor toward fabrication.
    """
    cv = index_text(cv_text)
    cv_lower = str(cv_text or "").lower()
    cv_toks = list(cv["tokens"])

    def tok_hit(t: str) -> bool:
        # Canonically equal, OR sharing a 5-char prefix. The prefix rule
        # collapses DERIVATIONAL forms the inflectional stemmer leaves apart
        # ("management" vs "managed" -> both "manag") without touching the
        # frozen stemmer. Only for tokens >=5 chars, so short abbreviations
        # (sql, aws, api) still need an exact canonical match.
        if t in cv["tokens"]:
            return True
        if len(t) < 5:
            return False
        p = t[:5]
        return any(len(c) >= 5 and c[:5] == p for c in cv_toks)

    matched, missing = [], []
    seen: set[str] = set()
    for raw in (skills or []):
        s = str("" if raw is None else raw).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        toks = tokens_of(s)
        if not toks:
            hit = key in cv_lower          # all-stopword skill: substring only
        else:
            hit = all(tok_hit(t["c"]) for t in toks) or key in cv_lower
        (matched if hit else missing).append(s)

    total = len(matched) + len(missing)
    return {
        "matched": matched,
        "missing": missing,
        "covered": len(matched),
        "total": total,
        "pct": js_round(len(matched) / total * 100) if total else 0,
    }
