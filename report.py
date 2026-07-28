"""Write the daily job queue CSV: data/job_queue_YYYY-MM-DD.csv.

Includes an `applied` column defaulting to "no" — the human submission
tracker. The tool never submits applications itself.
"""
import csv
import json
from datetime import date
from pathlib import Path

COLUMNS = ["applied", "applied_on", "match_feedback", "score", "title",
           "company", "location", "url",
           # Scoring detail (ported scoring stack, 2026-07-28). `score` is the
           # structured score; these explain it rather than restate it.
           "frozen_score", "legacy_score", "percentile", "band",
           "jd_difficulty", "must_coverage", "missing_must", "top_gaps",
           "score_flags", "sub_scores",
           "source", "apply_channel", "salary_min", "salary_max",
           "updated_at", "resume_docx", "resume_pdf",
           "tailored_summary", "bullets_to_lead_with",
           "rewritten_bullets",
           "keywords_to_add_if_true",
           "fit_exact", "fit_partial", "fit_gaps",
           "outreach_note", "change_log", "honest_gap_note",
           "missing_keywords", "description_snippet"]


def write_queue(jobs: list, out_dir: Path = Path("data")) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"job_queue_{date.today().isoformat()}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for j in jobs:
            t = j.get("tailored", {}) or {}
            fit = t.get("fit_analysis") or {}
            w.writerow({
                "applied": "no",
                "applied_on": "",
                # Your good/partial/no relevance label. Blank until you set it
                # in the dashboard; feedback.py learns from these.
                "match_feedback": "",
                "score": j.get("score", ""),
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "location": j.get("location", ""),
                "url": j.get("url", ""),
                "frozen_score": j.get("frozen_score", ""),
                "legacy_score": j.get("legacy_score", ""),
                "percentile": j.get("percentile", ""),
                "band": j.get("band", ""),
                "jd_difficulty": j.get("jd_difficulty", ""),
                "must_coverage": (
                    f"{(j.get('must_coverage') or {}).get('hit', 0)}/"
                    f"{(j.get('must_coverage') or {}).get('total', 0)}"
                    if j.get("must_coverage") else ""),
                "missing_must": ", ".join(j.get("missing_must", []) or []),
                "top_gaps": json.dumps(
                    [{"need": g["requirement"], "gain": g["delta"]}
                     for g in (j.get("top_gaps") or [])], ensure_ascii=False),
                "score_flags": "; ".join(j.get("score_flags", []) or []),
                # Per-job sub-scores: what feedback.py correlates your labels
                # against to work out which signals actually predict a match.
                "sub_scores": json.dumps(
                    {k: round(v, 4) for k, v in
                     (j.get("sub_scores") or {}).items()}, ensure_ascii=False),
                "source": j.get("source", ""),
                "apply_channel": j.get("apply_channel", ""),
                "salary_min": j.get("salary_min") or "",
                "salary_max": j.get("salary_max") or "",
                "updated_at": j.get("updated_at", ""),
                "resume_docx": j.get("resume_docx", ""),
                "resume_pdf": j.get("resume_pdf", ""),
                "tailored_summary": t.get("tailored_summary", ""),
                "bullets_to_lead_with":
                    json.dumps(t.get("bullets_to_lead_with", []),
                               ensure_ascii=False),
                "rewritten_bullets":
                    json.dumps(t.get("rewritten_bullets", []),
                               ensure_ascii=False),
                "keywords_to_add_if_true":
                    json.dumps(t.get("keywords_to_add_if_true", []),
                               ensure_ascii=False),
                "fit_exact":
                    json.dumps(fit.get("exact_matches", []),
                               ensure_ascii=False),
                "fit_partial":
                    json.dumps(fit.get("partial_matches", []),
                               ensure_ascii=False),
                "fit_gaps":
                    json.dumps(fit.get("gaps", []), ensure_ascii=False),
                "outreach_note": t.get("outreach_note", ""),
                "change_log": j.get("change_log", ""),
                "honest_gap_note": t.get("honest_gap_note", ""),
                "missing_keywords":
                    ", ".join(j.get("missing_keywords", [])),
                # long enough for the UI's on-demand tailoring to work with
                "description_snippet":
                    (j.get("description") or "")[:2000],
            })
    return path
