"""Deterministic structured CV parser — ported from cv-match-copilot's
lib/cvparse.js.

Turns raw resume text (plus the section map cv_parser.py already produces)
into structured, scorable data WITHOUT any LLM call:
  - sections:     header-detected segments (summary/skills/experience/...)
  - experience:   discrete entries with title, company, dates -> tenure
  - total_months: union of role intervals (overlaps not double-counted)
  - gaps:         unexplained stretches between roles, with length
  - skills:       DECLARED (skills section) vs DEMONSTRATED (mined from
                  experience bullets) — kept separate; they carry different
                  evidentiary weight downstream and must never be merged.

Everything here is best-effort and defensive: resumes are wildly inconsistent,
so when a signal isn't clearly present we return None/empty rather than guess.
A wrong parse must never invent experience.
"""
from __future__ import annotations

import re
from datetime import date

MONTHS = {
    "jan": 0, "january": 0, "feb": 1, "february": 1, "mar": 2, "march": 2,
    "apr": 3, "april": 3, "may": 4, "jun": 5, "june": 5, "jul": 6, "july": 6,
    "aug": 7, "august": 7, "sep": 8, "sept": 8, "september": 8, "oct": 9,
    "october": 9, "nov": 10, "november": 10, "dec": 11, "december": 11,
}

SECTION_HEADERS = [
    ("summary", re.compile(r"^(professional\s+)?(summary|profile|objective|about)\b", re.I)),
    ("skills", re.compile(r"^(technical\s+|core\s+|key\s+)?(skills|competenc|technolog|proficienc)", re.I)),
    ("experience", re.compile(r"^(work\s+|professional\s+|employment\s+)?(experience|history|employment)\b", re.I)),
    ("education", re.compile(r"^education\b", re.I)),
    ("projects", re.compile(r"^(key\s+|selected\s+)?projects\b", re.I)),
    ("certifications", re.compile(r"^(certifications?|licen[cs]es?|credentials)\b", re.I)),
    # Ported from lib/cvparse.js (added there 2026-07-29): without this,
    # achievements/awards were not a recognized section anywhere, so lines
    # like "Ranked 1st among 460+ teams" fell outside every section and the
    # audit compared JD numbers ("460") against text that structurally
    # couldn't contain them — a permanent false "missing" on every run.
    ("achievements", re.compile(
        r"^(key\s+|selected\s+)?(achievements?|awards?|honou?rs?|accomplishments?)\b", re.I)),
]

_SECTION_KEYS = ["summary", "skills", "experience", "education",
                 "projects", "certifications", "achievements"]


def now_index() -> int:
    d = date.today()
    return d.year * 12 + (d.month - 1)


def parse_month_year(s: str, now: int):
    """'Jan 2020' / 'January 2020' / '01/2020' / '2020' -> month index.
    'present'/'current'/'now'/'till date' -> `now`."""
    s = str(s or "").strip().lower()
    if not s:
        return None
    if re.search(r"present|current|now|till\s*date|to\s*date|ongoing", s):
        return now
    m = re.fullmatch(r"([a-z]{3,9})[.\s,]+(\d{4})", s)          # Month YYYY
    if m and m.group(1) in MONTHS:
        return int(m.group(2)) * 12 + MONTHS[m.group(1)]
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{4})", s)               # MM/YYYY
    if m:
        mo = int(m.group(1)) - 1
        if 0 <= mo <= 11:
            return int(m.group(2)) * 12 + mo
    m = re.fullmatch(r"(\d{4})", s)          # bare YYYY -> mid-year, low precision
    if m:
        return int(m.group(1)) * 12 + 6
    return None


# Shared date-token grammar. The month-name alternative is restricted to REAL
# month names — a greedy "[A-Za-z]{3,9} YYYY" would swallow an ordinary
# preceding word ("Strategy 2021") as a fake "Month YYYY", fail to parse it,
# and consume the valid bare-year range behind it.
MONTH_WORD = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
DATE_ALTS = MONTH_WORD + r"[.\s,]+\d{4}|\d{1,2}[/\-.]\d{4}|\d{4}"
DATE_TOKEN = r"(?:" + DATE_ALTS + r")"
END_TOKEN = r"(?:" + DATE_ALTS + r"|present|current|now|ongoing|till\s*date|to\s*date)"
SEP = r"\s*(?:[-–—]|to|until|till)\s*"
RANGE_RE = re.compile(r"(" + DATE_TOKEN + r")" + SEP + r"(" + END_TOKEN + r")", re.I)


def find_date_ranges(text: str, now: int | None = None) -> list[dict]:
    """Every 'start - end' date range in a block, as month-index intervals.
    Anything that doesn't parse cleanly is dropped rather than guessed."""
    now = now_index() if now is None else now
    out = []
    for m in RANGE_RE.finditer(str(text or "")):
        start = parse_month_year(m.group(1), now)
        ongoing = bool(re.search(r"present|current|now|ongoing|date", m.group(2), re.I))
        end = parse_month_year(m.group(2), now)
        if start is None or end is None:
            continue
        if end < start:
            continue                                  # garbled
        out.append({"start": start, "end": end, "ongoing": ongoing})
    return out


def union_months(intervals: list[dict]) -> int:
    """Union length in months — overlapping roles are not double-counted."""
    if not intervals:
        return 0
    s = sorted(intervals, key=lambda i: i["start"])
    total = 0
    cur_start, cur_end = s[0]["start"], s[0]["end"]
    for iv in s[1:]:
        if iv["start"] <= cur_end:
            cur_end = max(cur_end, iv["end"])
        else:
            total += cur_end - cur_start
            cur_start, cur_end = iv["start"], iv["end"]
    return total + (cur_end - cur_start)


def find_gaps(intervals: list[dict], threshold_months: int = 3) -> list[dict]:
    """Gaps longer than the threshold between consecutive roles. Reported, not
    judged — the caller decides whether to penalize, and education-explained
    gaps are marked so a penalty never fires on time spent on a degree."""
    threshold_months = threshold_months or 3
    if len(intervals) < 2:
        return []
    s = sorted(intervals, key=lambda i: i["start"])
    gaps = []
    prev_end = s[0]["end"]
    for iv in s[1:]:
        g = iv["start"] - prev_end
        if g >= threshold_months:
            gaps.append({"from": prev_end, "to": iv["start"], "months": g})
        prev_end = max(prev_end, iv["end"])
    return gaps


def segment_sections(cv_text: str, pdf_sections: dict | None = None) -> dict:
    """Header-based sectioning. Prefers the PDF extractor's section map when it
    supplies non-empty content; otherwise walks the raw text detecting headers."""
    out = {k: "" for k in _SECTION_KEYS}
    used = False
    if isinstance(pdf_sections, dict):
        for k in _SECTION_KEYS:
            v = pdf_sections.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()
                used = True
    if used:
        return out

    current = None
    for line in str(cv_text or "").split("\n"):
        line = line.strip()
        if not line:
            if current:
                out[current] += "\n"
            continue
        matched_header = None
        if len(line) <= 40:
            for name, rx in SECTION_HEADERS:
                if rx.search(line):
                    matched_header = name
                    break
        if matched_header:
            current = matched_header
            continue
        if current:
            out[current] += line + "\n"
    return {k: v.strip() for k, v in out.items()}


# Recognizes real bullet markers plus glyphs some PDF text-layers emit.
BULLET_RE = re.compile(r"^(?:[-–—•*·▪◦‣∙»]|\(cid:\d+\))\s*")


def is_bullet(line: str) -> bool:
    return bool(BULLET_RE.match(line.strip()))


def strip_bullet(line: str) -> str:
    return BULLET_RE.sub("", line.strip()).strip()


# Role-title cue words — tells a role line ("Product Manager ... 2023") from a
# bare employer header ("Bajaj Finance  2023-Present").
ROLE_KEYWORD = re.compile(
    r"\b(manager|engineer|developer|designer|analyst|consultant|lead|director"
    r"|head|officer|associate|specialist|architect|scientist|intern(ship)?"
    r"|president|founder|owner|principal|executive|coordinator|administrator"
    r"|strategist)\b", re.I)


def parse_experience(exp_text: str, now: int | None = None) -> list[dict]:
    """Split the experience section into discrete entries. A non-bullet line
    carrying a date range opens a new entry; following bullets attach to it.
    Best-effort across formats — never fabricates a role."""
    now = now_index() if now is None else now
    lines = [ln.rstrip() for ln in str(exp_text or "").split("\n")]
    entries: list[dict] = []
    cur = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        ranges = find_date_ranges(line, now)
        if ranges and not is_bullet(line):
            if cur:
                entries.append(cur)
            head_text = RANGE_RE.sub("", line)
            head_text = re.sub(r"[|,;·]\s*$", "", head_text).strip()
            # Split title/company only on STRONG separators. A bare hyphen is
            # left alone — it appears inside real titles ("Product Manager -
            # Home Loan"). Many CVs put the employer on its own header line, so
            # company is often empty here and filled by the umbrella-merge pass.
            parts = re.split(r"\s+[–—|]\s+|\s+at\s+|,\s+", head_text, flags=re.I)
            cur = {
                "title": (parts[0] if len(parts) > 0 else "").strip(),
                "company": (parts[1] if len(parts) > 1 else "").strip(),
                # Whether `company` came from splitting the role line rather
                # than from a real employer header. A dash inside a genuine
                # title ("Product Manager - Home Loan: Digital Platforms")
                # splits into a specialization, not an employer — so when an
                # umbrella employer is available it must win, and this half
                # belongs back on the title.
                "company_from_split": len(parts) > 1 and bool(parts[1].strip()),
                "start": ranges[0]["start"],
                "end": ranges[0]["end"],
                "ongoing": ranges[0]["ongoing"],
                "months": ranges[0]["end"] - ranges[0]["start"],
                "bullets": [],
                "is_employer_header": not ROLE_KEYWORD.search(head_text),
            }
        elif cur and is_bullet(line):
            cur["bullets"].append(strip_bullet(line))
        elif cur and not ranges:
            # A bare employer header ("Kantha India  May 2019 - Apr 2020")
            # whose ROLE TITLE sits on the next, undated line. The upstream JS
            # parser only handled the employer-then-dated-subrole shape, so
            # this CV lost "Digital Marketing Associate" and kept the company
            # name as the title. Adopt the title before the continuation
            # branch below can swallow it — guarded on the entry still being a
            # bulletless employer header, so a real wrapped bullet is untouched.
            if (cur.get("is_employer_header") and not cur["bullets"]
                    and not cur["company"] and len(line) <= 60
                    and ROLE_KEYWORD.search(line)):
                cur["company"] = cur["title"]
                cur["title"] = line
                cur["is_employer_header"] = False
            elif cur["bullets"]:
                # Wrapped continuation of the previous bullet.
                cur["bullets"][-1] += " " + line
    if cur:
        entries.append(cur)
    return merge_employer_headers(entries)


def merge_employer_headers(entries: list[dict]) -> list[dict]:
    """Collapse the nested 'employer header + sub-roles' pattern. An entry with
    no bullets that looks like a bare employer and is followed by dated roles
    inside its span is an EMPLOYER header, not a job — drop it as a standalone
    role and stamp its name onto the sub-roles lacking a company. Otherwise it
    double-counts as a phantom 0-bullet role and the real roles lose their
    employer."""
    out = []
    for i, e in enumerate(entries):
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        is_umbrella = (
            e.get("is_employer_header") and not e["bullets"] and nxt
            and nxt["start"] >= e["start"] - 1 and nxt["end"] <= e["end"] + 1)
        if is_umbrella:
            employer = e["title"]
            for j in range(i + 1, len(entries)):
                if (entries[j]["start"] >= e["start"] - 1
                        and entries[j]["end"] <= e["end"] + 1):
                    sub = entries[j]
                    if not sub["company"]:
                        sub["company"] = employer
                    elif sub.get("company_from_split"):
                        # The "company" is really the tail of the title; the
                        # umbrella is the actual employer.
                        sub["title"] = f"{sub['title']} - {sub['company']}".strip(" -")
                        sub["company"] = employer
                        sub["company_from_split"] = False
                else:
                    break
            continue                     # drop the umbrella as a standalone entry
        out.append(e)
    for e in out:
        e.pop("is_employer_header", None)
        e.pop("company_from_split", None)
    return out


def extract_skills(sections: dict, experience: list[dict]) -> dict:
    """Declared skills = items in the skills section. Demonstrated = declared
    skills that ALSO appear in an experience bullet, tagged with how many
    bullets evidence them and the most-recent evidencing role's end (for
    recency decay). Kept separate — they carry different evidentiary weight."""
    declared_raw = []
    for s in re.split(r"[,\n;|•·▪◦]|\s{2,}", str(sections.get("skills") or "")):
        s = re.sub(r"^[-–—*\s]+", "", s).strip()
        # Strip a leading category label ("Product & Delivery: Product
        # Management" -> "Product Management"). Guarded on a short, word-only
        # label so we never eat a colon inside a real skill.
        m = re.fullmatch(r"([A-Za-z][A-Za-z &/]{1,28}):\s*(.+)", s)
        if m:
            s = m.group(2).strip()
        if 2 <= len(s) <= 40:
            declared_raw.append(s)

    declared, seen = [], set()
    for s in declared_raw:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            declared.append(s)

    def bullet_evidences(skill: str, bullet: str) -> bool:
        """Whole phrase verbatim OR all of its significant words (>=4 chars).
        Looser than exact substring — a bare substring test finds almost
        nothing on real CVs, whose bullets describe outcomes, not skill names."""
        b = bullet.lower()
        kl = skill.lower()
        if kl in b:
            return True
        words = [w for w in re.split(r"[^a-z0-9+#]+", kl) if len(w) >= 4]
        if not words:
            return False
        return all(w in b for w in words)

    demonstrated = []
    for skill in declared:
        evidence = []
        recent_end = None
        for ri, role in enumerate(experience):
            for b in role.get("bullets", []):
                if bullet_evidences(skill, b):
                    evidence.append({"role_index": ri, "text": b})
                    if recent_end is None or role["end"] > recent_end:
                        recent_end = role["end"]
        if evidence:
            demonstrated.append({"skill": skill, "count": len(evidence),
                                 "recent_end": recent_end, "evidence": evidence})
    return {"declared": declared, "demonstrated": demonstrated}


def parse_education(edu_text: str) -> list[dict]:
    """'Institution -- Degree | Spec 2021 - 2023' -> structured entries. Only
    lines with a parseable year range or a degree keyword count."""
    out = []
    for line in str(edu_text or "").split("\n"):
        line = line.strip()
        if not line or re.fullmatch(r"education", line, re.I):
            continue
        years = re.search(r"(\d{4})\s*[-–—]\s*(\d{4})", line)
        body = re.sub(r"(\d{4})\s*[-–—]\s*(\d{4})", "", line).strip()
        parts = re.split(r"\s+--\s+|\s+—\s+|\s+\|\s+", body)
        institution = (parts[0] if parts else "").strip()
        degree = re.sub(r"[|,]\s*$", "", ", ".join(parts[1:])).strip()
        if not institution:
            continue
        if not years and not re.search(
                r"(bachelor|master|mba|b\.?tech|m\.?tech|b\.?e\b|diploma|phd"
                r"|b\.?sc|m\.?sc|graduat)", line, re.I):
            continue
        out.append({
            "institution": institution,
            "degree": degree,
            "start_year": years.group(1) if years else "",
            "end_year": years.group(2) if years else "",
        })
    return out


def parse_cv_structured(cv_text: str, pdf_sections: dict | None = None,
                        now: int | None = None) -> dict:
    """Full deterministic parse of a CV."""
    now = now_index() if now is None else now
    sections = segment_sections(cv_text, pdf_sections)
    experience = parse_experience(sections.get("experience") or cv_text, now)
    intervals = [{"start": e["start"], "end": e["end"]} for e in experience]
    total_months = union_months(intervals)
    latest_role_end = max((e["end"] for e in experience), default=None)

    # Fairness, applied at parse time: mark employment gaps that overlap a
    # period of study as EXPLAINED, so a downstream gap penalty never fires on
    # time spent on a degree. A full-time MBA is a legitimate reason for a gap.
    edu_intervals = find_date_ranges(sections.get("education") or "", now)
    gaps = []
    for g in find_gaps(intervals, 3):
        explained_by = None
        for ed in edu_intervals:
            if ed["start"] < g["to"] and ed["end"] > g["from"]:
                explained_by = "education"
                break
        g["explained"] = bool(explained_by)
        g["reason"] = explained_by
        gaps.append(g)

    return {
        "sections": sections,
        "experience": experience,
        "role_count": len(experience),
        "total_months": total_months,
        "total_years": round(total_months / 12 * 10) / 10,
        "gaps": gaps,
        "unexplained_gaps": [g for g in gaps if not g["explained"]],
        "education_intervals": edu_intervals,
        "latest_role_end": latest_role_end,
        "education": parse_education(sections.get("education", "")),
        "skills": extract_skills(sections, experience),
        "now": now,
    }
