"""Job sources. Every module normalizes listings to the same dict shape:

{
  "source": str, "company": str, "title": str, "location": str,
  "description": str, "url": str, "updated_at": str,
  "salary_min": float|None, "salary_max": float|None,
}
"""


def normalize(source, company, title, location, description, url,
              updated_at="", salary_min=None, salary_max=None, **extra):
    row = {
        "source": source,
        "company": (company or "").strip(),
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "description": (description or "").strip(),
        "url": (url or "").strip(),
        "updated_at": updated_at or "",
        "salary_min": salary_min,
        "salary_max": salary_max,
    }
    row.update(extra)
    return row
