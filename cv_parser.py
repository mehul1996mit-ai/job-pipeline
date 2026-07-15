"""Parse the base CV (base_cv.pdf) into text, sections, bullets, keywords.

Matching downstream uses the FULL cv text (every bullet), never just the
current job title.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

BULLET_CHARS = ("•", "▪", "●", "‣", "", "-", "*", "�")

SECTION_HEADINGS = {
    "summary": ("summary", "profile", "about"),
    "skills": ("skills", "core competencies", "technical skills"),
    "experience": ("professional experience", "experience", "work experience",
                   "employment"),
}

NOISE_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "will", "have", "has",
    "are", "was", "were", "you", "your", "our", "their", "them", "they", "its",
    "all", "any", "can", "may", "not", "but", "who", "what", "when", "where",
    "how", "why", "than", "then", "into", "onto", "over", "under", "more",
    "most", "other", "such", "each", "per", "via", "etc", "including",
    "include", "includes", "included", "across", "within", "about", "also",
    "able", "well", "work", "working", "role", "job", "team", "teams",
    "company", "years", "year", "experience", "candidate", "responsibilities",
    "requirements", "qualifications", "preferred", "required", "strong",
    "skills", "ability", "knowledge", "must", "should", "would", "like",
    "look", "looking", "join", "opportunity", "based", "using", "use", "new",
    "key", "end", "ensure", "drive", "help", "part", "day", "www", "http",
    "https", "com",
}


@dataclass
class ParsedCV:
    raw_text: str
    summary: str
    skills: str
    experience: str
    bullets: list[str] = field(default_factory=list)
    keywords: set[str] = field(default_factory=set)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z0-9+#./-]{2,}", (text or "").lower())


def keyword_set(text: str) -> set[str]:
    return {t.strip("./-") for t in tokenize(text)} - NOISE_WORDS


def _extract_text(pdf_path: Path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _find_sections(text: str) -> dict[str, str]:
    """Split text into sections keyed by canonical heading name."""
    lines = text.splitlines()
    # Identify heading lines: short lines matching a known heading keyword.
    marks = []  # (line_index, canonical_name)
    for i, line in enumerate(lines):
        clean = line.strip().lower().rstrip(":")
        if len(clean) > 40:
            continue
        for canon, variants in SECTION_HEADINGS.items():
            if any(clean.startswith(v) for v in variants):
                marks.append((i, canon))
                break
        else:
            # any ALL-CAPS short line ends the previous section too
            if clean and line.strip().isupper():
                marks.append((i, None))
    sections: dict[str, str] = {"summary": "", "skills": "", "experience": ""}
    for idx, (start, canon) in enumerate(marks):
        if canon is None:
            continue
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        body = "\n".join(lines[start + 1:end]).strip()
        if not sections[canon]:
            sections[canon] = body
    return sections


def _extract_bullets(text: str) -> list[str]:
    bullets = []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                bullets.append(current)
                current = None
            continue
        if stripped.startswith(BULLET_CHARS) and len(stripped) > 3:
            if current:
                bullets.append(current)
            current = stripped.lstrip("".join(BULLET_CHARS)).strip()
        elif current is not None and not stripped.isupper():
            # continuation line of a wrapped bullet
            current += " " + stripped
        else:
            if current:
                bullets.append(current)
                current = None
    if current:
        bullets.append(current)
    return [b for b in bullets if len(b) > 15]


def parse_cv(pdf_path: str | Path = "base_cv.pdf") -> ParsedCV:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"{pdf_path} not found. Place your resume as base_cv.pdf in the "
            "repo root.")
    raw = _extract_text(pdf_path)
    sections = _find_sections(raw)
    return ParsedCV(
        raw_text=raw,
        summary=sections["summary"],
        skills=sections["skills"],
        experience=sections["experience"],
        bullets=_extract_bullets(raw),
        keywords=keyword_set(raw),
    )


if __name__ == "__main__":
    cv = parse_cv()
    print(f"chars={len(cv.raw_text)} bullets={len(cv.bullets)} "
          f"keywords={len(cv.keywords)}")
    print("summary:", (cv.summary[:200] or "<none>"))
