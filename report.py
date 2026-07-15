"""Write the daily job queue CSV: data/job_queue_YYYY-MM-DD.csv.

Includes an `applied` column defaulting to "no" — the human submission
tracker. The tool never submits applications itself.
"""
import csv
import json
from datetime import date
from pathlib import Path

COLUMNS = ["applied", "score", "title", "company", "location", "url",
           "source", "salary_min", "salary_max", "updated_at",
           "tailored_summary", "bullets_to_lead_with",
           "keywords_to_add_if_true", "honest_gap_note",
           "missing_keywords", "description_snippet"]


def write_queue(jobs: list, out_dir: Path = Path("data")) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"job_queue_{date.today().isoformat()}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for j in jobs:
            t = j.get("tailored", {}) or {}
            w.writerow({
                "applied": "no",
                "score": j.get("score", ""),
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "location": j.get("location", ""),
                "url": j.get("url", ""),
                "source": j.get("source", ""),
                "salary_min": j.get("salary_min") or "",
                "salary_max": j.get("salary_max") or "",
                "updated_at": j.get("updated_at", ""),
                "tailored_summary": t.get("tailored_summary", ""),
                "bullets_to_lead_with":
                    json.dumps(t.get("bullets_to_lead_with", []),
                               ensure_ascii=False),
                "keywords_to_add_if_true":
                    json.dumps(t.get("keywords_to_add_if_true", []),
                               ensure_ascii=False),
                "honest_gap_note": t.get("honest_gap_note", ""),
                "missing_keywords":
                    ", ".join(j.get("missing_keywords", [])),
                "description_snippet":
                    (j.get("description") or "")[:400],
            })
    return path
