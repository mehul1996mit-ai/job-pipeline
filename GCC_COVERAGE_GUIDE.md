# GCC Coverage Guide — the manual layers

The automated pipeline covers Adzuna (aggregator), the open Workday CXS feeds
(Citi, Deutsche Bank, Wells Fargo), and any Greenhouse/Lever boards you add.
Several high-value GCC employers **cannot be polled automatically** — their
career portals are bot-protected or have no public JSON feed. Cover them with
the two manual layers below. Budget: ~20 minutes per week.

## Layer 1 — Native email alerts (set up once)

Create a job alert directly on each portal. These arrive by email the moment
a role is posted — faster than any scraper, and fully within each site's
terms.

| Employer | Where | Suggested alert |
|---|---|---|
| HSBC | careers.hsbc.com (Avature — protected) | "Product Manager" + "Business Analyst", India |
| Barclays | search.jobs.barclays (protected) | "Product" + "Analyst", Pune |
| JPMorgan Chase | careers.jpmorgan.com (Oracle — protected) | "Product Manager", Mumbai/Bangalore |
| Goldman Sachs | higher.gs.com (protected) | "Product Manager", Bengaluru/Hyderabad |
| Morgan Stanley | morganstanley.com/careers (protected Workday) | "Product", Mumbai |
| UBS | jobs.ubs.com (protected) | "Business Analyst", Pune/Mumbai |
| Standard Chartered | scb.taleo.net (protected) | "Product Owner", Bangalore/Chennai-remote |
| Amex | careers.americanexpress.com | "Product Manager", Gurgaon/Bangalore |

Tips:
- Use a dedicated email folder/filter (`from:(careers OR noreply) job alert`)
  so alerts don't drown in your inbox.
- When an alert lands, paste the JD into the queue CSV manually and run the
  tailoring prompt on it (or ask your assistant to tailor against `base_cv.pdf`).

## Layer 2 — Weekly iimjobs / Naukri saved-search routine (every Monday)

These boards require login, so the pipeline never touches them — you do this
by hand, once a week:

1. **iimjobs.com** — save these searches and open each (sorted by date,
   filter: 4–8 yrs):
   - "Product Manager" | Pune, Mumbai, Bangalore, Remote
   - "Business Analyst fintech"
   - "Program Manager lending"
   - "Partnerships Manager"
2. **Naukri.com** — maintain 4 saved searches (max on free tier) with email
   alerts ON:
   - Product Manager, 4–8 yrs, Pune/Mumbai/Bangalore
   - Product Owner, 4–8 yrs, same cities
   - Business Analyst BFSI, 4–8 yrs
   - Growth Manager, 4–8 yrs
3. For each shortlisted role: add a row to today's `job_queue_*.csv`
   (applied="no"), tailor, and apply the same day — Naukri recruiter searches
   heavily favor recent applicants.
4. Keep your Naukri profile "modified" weekly (even a one-character headline
   edit) — recency boosts recruiter-search ranking.

## Why these stay manual (do not automate them)

Scraping logged-in boards or bot-protected portals violates their terms,
risks your accounts, and produces worse applications than the alert+review
flow above. The pipeline's job is to make the review fast, not to bypass the
platforms. See the design boundary in `main.py` and README — final
submission is always a human action.
