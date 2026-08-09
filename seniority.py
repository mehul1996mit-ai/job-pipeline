"""Experience-requirement extraction and seniority judgement.

WHY THIS EXISTS (2026-08-09). Titles are a bad proxy for seniority and always
will be: "VP" at State Street/Natwest is a ~6-10y individual-contributor band,
"VP" at a startup is a founder, and "Product Owner" spans 2-12y depending on
the company. Filtering on title strings trades one wrong answer for another.
What actually decides fit is the experience the posting REQUIRES — so that is
what this module extracts, and it is explicit about how much it trusts each
answer instead of pretending one number fits all sources.

Measured on the real 2026-08-01..09 queues (185 jobs) before this existed:
only 13% had a parseable experience band, and the parser in matcher.py missed
every one of "7yrs", "minimum of 5 years", "Experience : 11 years".

THREE CONFIDENCE TIERS, because they must not be treated alike:
  stated   — a real requirement phrase was found in the JD. Trustworthy.
  repaired — a mangled range was reconstructed (see MANGLED RANGES below).
             Very likely right, but it IS a reconstruction.
  inferred — no number anywhere; a band implied from title/JD wording only.
             A hint, never grounds to hard-reject (see the fairness note).

MANGLED RANGES. Adzuna's own API — not this pipeline — ships descriptions
with the dash removed from ranges: verified live, their API returns the
literal string "Experience: 48 years" for a posting that plainly means 4-8,
and "810"/"712"/"37" for 8-10/7-12/3-7. Confirmed it is upstream by fetching
the API directly and by checking the raw CSV bytes (no dash byte present).
Their data is INCONSISTENT, not uniformly broken — plenty of other Adzuna
rows carry proper en-dashes ("8-15 Years", "4-7 Years") — so this repairs
rather than assumes. Nobody requires 48 years of experience, so a value above
IMPLAUSIBLE_YEARS is treated as a glued-together range and split only when
the split yields a sane ascending band.

COMPANY AGE IS NOT A REQUIREMENT. The single biggest false-positive risk: real
descriptions say "founded over 180 years ago", "a 35-year veteran of the
loyalty industry", "45 years of experience and a presence across 10
countries". A naive number-near-"years" match reads all of those as job
requirements. NEGATIVE_CONTEXT_RE exists to kill them and should be extended,
never loosened, when a new false positive shows up.

FAIRNESS. This module reports; it does not silently reject. `judge()` returns
a verdict and the caller decides. That mirrors the standing rule in
aggregate.py/smoke_test.py: a checkable gate may hard-cap a score, an
UNVERIFIABLE one is flagged for a human, never auto-failed. An inferred band
is by definition unverifiable.
"""
import re

# A number this large is never a job requirement — it is either company age
# (caught below) or a range whose separator was destroyed upstream.
IMPLAUSIBLE_YEARS = 25
# Widest sane single-number requirement; used when repairing a glued range.
MAX_SANE_BAND = 30

# Phrases where a number near "years" describes the COMPANY, a tenure record,
# an application limit — anything but what the candidate must have.
NEGATIVE_CONTEXT_RE = re.compile(
    r"(founded|established|incorporated|since\s+\d{4}|years?\s+ago|"
    r"\bhistory\b|heritage|legacy|anniversary|veteran|in\s+business|"
    r"serving\s+(?:clients|customers)|presence\s+(?:across|in)|"
    r"operating\s+(?:for|in)|over\s+the\s+(?:past|last)|"
    r"within\s+\d{1,2}\s+months|maximum\s+of|\bage\b|aged)", re.I)

# Ordered most-specific first. Each returns (lo, hi) or (lo, None).
_RANGE = (
    r"(?<!\d)(\d{1,2})\s*(?:-|--|-|—|–|to|~)\s*(\d{1,2})\s*\+?\s*"
    r"(?:\+\s*)?(?:years?|yrs?)")
_MIN_WORDED = (
    r"(?:minimum|min\.?|at\s*least|atleast|no\s+less\s+than)\s*"
    r"(?:of\s*)?(?<!\d)(\d{1,2})\s*\+?\s*(?:years?|yrs?)")
_PLUS = r"(?<!\d)(\d{1,2})\s*\+\s*(?:years?|yrs?)"
# "Experience: 4-8 Years" / "Experience : 11 years" / "Exp: 7yrs"
_LABELLED = (
    r"(?:experience|exp|exp\.)\s*[:\-]\s*(?<!\d)(\d{1,4})\s*"
    r"(?:(?:-|—|–|to)\s*(\d{1,2}))?\s*\+?\s*(?:years?|yrs?)?")
# "5 years of relevant experience", "7 yrs in customer success"
_TRAILING = (
    r"(?<!\d)(\d{1,4})\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?"
    r"(?:relevant\s+|related\s+|total\s+|proven\s+|hands[- ]on\s+|"
    r"professional\s+|work\s+)?(?:experience|exp\b|in\b|working\b|as\b)")

_PATTERNS = [
    ("range", re.compile(_RANGE, re.I)),
    ("labelled", re.compile(_LABELLED, re.I)),
    ("min_worded", re.compile(_MIN_WORDED, re.I)),
    ("plus", re.compile(_PLUS, re.I)),
    ("trailing", re.compile(_TRAILING, re.I)),
]

# Seniority implied by title wording, used ONLY when no number exists.
# Bands are deliberately wide — this tier is a hint, not a measurement.
_TITLE_TIERS = [
    ("exec", re.compile(
        r"\b(chief|c[teifo]o\b|cxo|svp|evp|executive\s+vice|"
        r"managing\s+director|\bmd\b|partner|general\s+manager|\bgm\b|"
        r"head\s+of|global\s+head|country\s+head|director)\b", re.I), (12, 99)),
    ("lead", re.compile(
        r"\b(lead|principal|staff|group\s+product\s+manager|\bgpm\b|"
        r"senior\s+manager|architect)\b", re.I), (8, 14)),
    ("ic_senior", re.compile(r"\b(senior|sr\.?|ii+|advanced)\b", re.I), (6, 10)),
    ("ic_junior", re.compile(
        r"\b(junior|jr\.?|associate|trainee|intern|graduate|entry[\s-]?level|"
        r"fresher|apprentice)\b", re.I), (0, 3)),
]
# "VP" alone is genuinely ambiguous — an IC band at banks (State Street,
# Natwest, Citi all use it that way, and those are sources this pipeline
# polls directly) but an executive title nearly everywhere else. Refusing to
# pick is the honest answer: it widens the band and stays low-confidence
# rather than inventing a precision this signal does not have.
_VP_RE = re.compile(r"\bvp\b|\bvice\s+president\b", re.I)
_VP_BAND = (6, 14)


def _sane(lo, hi):
    return lo is not None and 0 <= lo <= MAX_SANE_BAND and (
        hi is None or (lo <= hi <= 99))


def _repair_glued(n: int):
    """Split a number too large to be a real requirement back into the range
    whose separator was destroyed upstream. 48->(4,8), 810->(8,10),
    712->(7,12), 1015->(10,15). Returns None when no split is sensible, so a
    genuinely odd number is dropped rather than guessed at."""
    s = str(n)
    for cut in range(1, len(s)):
        lo, hi = int(s[:cut]), int(s[cut:])
        # Ascending, both plausible, and not a leading-zero artefact.
        if 0 < lo < hi <= MAX_SANE_BAND and not s[cut:].startswith("0"):
            return lo, hi
    return None


def _window(text: str, start: int, end: int, pad: int = 60) -> str:
    return text[max(0, start - pad):min(len(text), end + pad)]


def extract_experience(title: str, jd_text: str) -> dict:
    """Best available read of the experience this posting requires.

    Returns {min_years, max_years, confidence, evidence, seniority}.
    confidence is one of: stated | repaired | inferred | unknown.
    """
    text = f"{title or ''}\n{jd_text or ''}"
    out = {"min_years": None, "max_years": None, "confidence": "unknown",
           "evidence": "", "seniority": ""}

    for kind, rx in _PATTERNS:
        for m in rx.finditer(text):
            ctx = _window(text, m.start(), m.end())
            if NEGATIVE_CONTEXT_RE.search(ctx):
                continue  # company age / tenure record / application limit
            g = [x for x in m.groups() if x is not None]
            if not g:
                continue
            lo = int(g[0])
            hi = int(g[1]) if len(g) > 1 else None

            if hi is None and lo > IMPLAUSIBLE_YEARS:
                rep = _repair_glued(lo)
                if not rep:
                    continue
                lo, hi = rep
                out.update(min_years=lo, max_years=hi, confidence="repaired",
                           evidence=" ".join(m.group(0).split()))
                return _with_seniority(out, title)

            if not _sane(lo, hi):
                continue
            if kind in ("min_worded", "plus"):
                hi = None  # "5+ years" states a floor, not a ceiling
            out.update(min_years=lo, max_years=hi, confidence="stated",
                       evidence=" ".join(m.group(0).split()))
            return _with_seniority(out, title)

    # Nothing stated anywhere — fall back to the title's own wording.
    band, tier = _infer_from_title(title)
    if band:
        out.update(min_years=band[0], max_years=band[1],
                   confidence="inferred", seniority=tier,
                   evidence=f"inferred from title wording ({tier})")
    return out


def _infer_from_title(title: str):
    t = title or ""
    for tier, rx, band in _TITLE_TIERS:
        if rx.search(t):
            # An explicit "senior"/"director" beats the bare-VP ambiguity.
            return band, tier
    if _VP_RE.search(t):
        return _VP_BAND, "vp_ambiguous"
    return None, ""


def _with_seniority(out: dict, title: str) -> dict:
    _, tier = _infer_from_title(title)
    out["seniority"] = tier
    return out


def judge(band: dict, my_years: float, comfort_max: float = 8.0,
          stretch: float = 2.0) -> dict:
    """Turn an extracted band into a decision.

    comfort_max — the most experience a posting may ask for and still be
    worth surfacing (the user's own ceiling, not a property of the job).
    stretch     — how far above `my_years` a floor may sit and still count as
                  a reachable stretch rather than out of range.

    verdict: good_fit | stretch | over_senior | under_senior | unknown
    """
    lo, hi = band.get("min_years"), band.get("max_years")
    conf = band.get("confidence", "unknown")
    if lo is None:
        return {"verdict": "unknown", "confidence": conf,
                "why": "no experience requirement found in the available text"}

    # A STATED floor is a real gate — you must have at least `lo`, so `lo` is
    # what to judge against. An INFERRED band is not a gate at all; it is a
    # guess at where the role sits, and judging it on its floor systematically
    # under-reads seniority. Concretely: a bare "VP" infers 6-14, whose floor
    # (6) reads as a comfortable fit while its centre (10) correctly reads as
    # too senior — and the VP postings are exactly the ones that prompted
    # this. So an inferred band is judged on its midpoint instead.
    if conf == "inferred" and hi is not None:
        centre = (lo + hi) / 2 if hi < 99 else lo
        if centre > comfort_max:
            return {"verdict": "over_senior", "confidence": conf,
                    "why": (f"title implies roughly {_fmt(lo, hi)} "
                            f"(centre ~{centre:g}y), above your "
                            f"{comfort_max:g}-year ceiling — inferred from "
                            f"wording, not stated in the posting")}

    # A floor far above both your experience AND your ceiling is the clearest
    # signal there is — this is the "VP wants 12+ years" case.
    if lo > comfort_max:
        return {"verdict": "over_senior", "confidence": conf,
                "why": f"asks for {_fmt(lo, hi)}, above your {comfort_max:g}-year ceiling"}
    if lo > my_years + stretch:
        return {"verdict": "stretch", "confidence": conf,
                "why": f"asks for {_fmt(lo, hi)} vs your {my_years:g}"}
    # A ceiling well below your experience means they want someone cheaper /
    # more junior; worth flagging, not worth hiding.
    if hi is not None and my_years > hi + 3:
        return {"verdict": "under_senior", "confidence": conf,
                "why": f"caps at {hi:g} years vs your {my_years:g}"}
    return {"verdict": "good_fit", "confidence": conf,
            "why": f"asks for {_fmt(lo, hi)}, you have {my_years:g}"}


def _fmt(lo, hi):
    if hi is None:
        return f"{lo:g}+ years"
    return f"{lo:g}-{hi:g} years"
