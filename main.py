"""Daily unattended pipeline: PARSE -> SEARCH -> MATCH & SCORE -> TAILOR & DELIVER.

Design boundary (do not remove): this tool PREPARES applications — links,
tailored materials, honest gap notes. Submitting an application is always a
human action. It never logs into job portals, never auto-fills third-party
forms unattended, and never bypasses CAPTCHA/bot-detection.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import pdfplumber
import yaml

import dedupe
import matcher
import notify
import report
import resume_render
import scoring_core
import skill_match
import tailor as tailor_mod
import tracker
from cv_parser import parse_cv, keyword_set
from cv_structure import parse_cv_structured
from sources import (adzuna, ashby, greenhouse, job_alert_email, lever,
                     serpapi_jobs, smartrecruiters, workday)


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(s)).strip("_")[:40]


def log(msg):
    print(msg, flush=True)


def load_config(path="config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    config = load_config()

    # ---------------------------------------------------------- 1. PARSE
    log("== 1/4 PARSE: reading base_cv.pdf")
    cv = parse_cv("base_cv.pdf")
    log(f"   cv: {len(cv.raw_text)} chars, {len(cv.bullets)} bullets, "
        f"{len(cv.keywords)} keywords")
    # Structured parse — roles/tenure/gaps/declared-vs-demonstrated skills.
    # Deterministic and done ONCE per run; every job is scored against it.
    structured_cv = parse_cv_structured(cv.raw_text, cv.section_map())
    log(f"   structured: {structured_cv['role_count']} roles, "
        f"{structured_cv['total_years']} yrs, "
        f"{len(structured_cv['skills']['declared'])} declared / "
        f"{len(structured_cv['skills']['demonstrated'])} demonstrated skills, "
        f"{len(structured_cv['unexplained_gaps'])} unexplained gap(s)")
    master_resume = json.loads(
        Path("resume_master.json").read_text(encoding="utf-8"))

    # Precomputed ONCE: the CV is constant for the whole run, but
    # matcher.score_job() is called up to 3x per job listing (initial pass,
    # Workday full-JD rescore, post-tailoring rescore). Without this, both
    # scoring layers re-tokenize the same CV text from scratch on every one
    # of those calls, hundreds to thousands of times a day.
    cv_index = scoring_core.index_text(cv.raw_text)
    _skill_sections = structured_cv.get("sections") or {}
    _skill_cv_text = "\n".join(_skill_sections.get(k, "") for k in _skill_sections)
    skill_cv_index = skill_match.index_layers(_skill_cv_text)
    skill_cv_lower = _skill_cv_text.lower()

    # --------------------------------------------------------- 2. SEARCH
    log("== 2/4 SEARCH: pulling live listings")
    jobs = []
    for module in (adzuna, workday, greenhouse, lever, smartrecruiters, ashby,
                   job_alert_email, serpapi_jobs):
        try:
            jobs.extend(module.fetch(config, log=log))
        except Exception as e:  # a whole-source failure must not kill the run
            log(f"   source {module.__name__} failed entirely ({e}) — "
                "continuing")
    log(f"   total raw listings: {len(jobs)}")

    # -------------------------------------------------- 3. MATCH & SCORE
    log("== 3/4 MATCH & SCORE")
    jobs = dedupe.dedupe_cross_source(jobs)
    log(f"   after cross-source dedupe: {len(jobs)}")

    jobs = [j for j in jobs if matcher.passes_filters(j, config)]
    log(f"   after filters: {len(jobs)}")

    seen = dedupe.load_seen()
    new_jobs = dedupe.filter_new(jobs, seen)
    log(f"   NEW (not in seen-store): {len(new_jobs)}")

    def rescore(j):
        """Full scoring stack for one job. Deterministic — no API calls — so
        it is safe to run on every listing and again after a full JD arrives."""
        jd_text = f"{j['title']} {j.get('description', '')}"
        # title passed separately: seniority inference must read the TITLE's
        # own wording, not stray "senior"/"director" mentions in the body.
        r = matcher.score_job(jd_text, cv.raw_text, structured_cv, config,
                              title=j.get("title", ""),
                              cv_index=cv_index, skill_cv_index=skill_cv_index,
                              skill_cv_lower=skill_cv_lower)
        j.update({
            "score": r["score"],
            "exp_min_years": r["exp_min_years"],
            "exp_max_years": r["exp_max_years"],
            "exp_confidence": r["exp_confidence"],
            "exp_verdict": r["exp_verdict"],
            "exp_why": r["exp_why"],
            "seniority_tier": r["seniority_tier"],
            "frozen_score": r["frozen_score"],
            "legacy_score": matcher.ats_score(jd_text, cv.keywords, config),
            "percentile": r["percentile"],
            "band": r["band"],
            "jd_difficulty": r["jd_difficulty"],
            "must_coverage": r["must_coverage"],
            "missing_must": r["missing_must"],
            "top_gaps": r["top_gaps"],
            "score_flags": r["flags"],
            "sub_scores": r["sub_scores"],
            "jd_analysis": r["analysis"],
        })

    for j in new_jobs:
        rescore(j)
    new_jobs.sort(key=lambda j: j["score"], reverse=True)

    # Politeness cap: full JDs for the top N Workday matches only, then
    # re-score them on the complete text.
    detail_n = int(config.get("workday", {}).get("detail_top_n", 8))
    wd_top = [j for j in new_jobs if j["source"] == "workday"][:detail_n]
    if wd_top:
        log(f"   fetching full JDs for top {len(wd_top)} Workday matches")
        for j in wd_top:
            full = workday.fetch_job_detail(
                j["workday_tenant"], j["workday_external_path"], log=log)
            if full:
                j["description"] = full
                rescore(j)      # full JD -> real requirement extraction
            time.sleep(1.0)
        new_jobs.sort(key=lambda j: j["score"], reverse=True)

    for j in new_jobs:
        j["missing_keywords"] = matcher.matched_keywords(
            j["description"], cv.keywords)

    # ------------------------------------------------ 4. TAILOR & DELIVER
    log("== 4/4 TAILOR & DELIVER")
    top_n = int(config.get("tailor", {}).get("top_n", 25))
    score_floor = int(
        config.get("filters", {}).get("min_score_to_tailor", 0) or 0)
    eligible = [j for j in new_jobs if j["score"] >= score_floor]
    skipped_weak = len(new_jobs[:top_n]) - len(eligible[:top_n])
    to_tailor = eligible[:top_n]
    if skipped_weak > 0:
        log(f"   score floor {score_floor}: skipping weak fits that would "
            f"otherwise be tailored")
    log(f"   tailoring top {len(to_tailor)} (score >= {score_floor}) via "
        f"provider '{config.get('tailor', {}).get('provider')}'")
    resumes_dir = Path("data") / "resumes" / date.today().isoformat()
    for i, j in enumerate(to_tailor, 1):
        j["tailored"] = tailor_mod.tailor_job(
            cv.raw_text, j, j["missing_keywords"], config, log=log)

        # The tailoring call also returns the model's read of the POSTING's
        # requirements. Re-score with it: a model that actually read the JD
        # classifies must-have vs preferred far better than the regex analyst,
        # and this costs no extra API call. A failed/empty analysis leaves the
        # deterministic score untouched (see jd_analyst.merge_llm_analysis).
        llm_analysis = (j["tailored"] or {}).get("jd_analysis")
        if llm_analysis:
            before = j["score"]
            r = matcher.score_job(
                f"{j['title']} {j.get('description', '')}", cv.raw_text,
                structured_cv, config, llm_analysis=llm_analysis,
                title=j.get("title", ""),
                cv_index=cv_index, skill_cv_index=skill_cv_index,
                skill_cv_lower=skill_cv_lower)
            j.update({
                "score": r["score"], "percentile": r["percentile"],
                "exp_min_years": r["exp_min_years"],
                "exp_max_years": r["exp_max_years"],
                "exp_confidence": r["exp_confidence"],
                "exp_verdict": r["exp_verdict"], "exp_why": r["exp_why"],
                "seniority_tier": r["seniority_tier"],
                "band": r["band"], "jd_difficulty": r["jd_difficulty"],
                "must_coverage": r["must_coverage"],
                "missing_must": r["missing_must"], "top_gaps": r["top_gaps"],
                "score_flags": r["flags"], "sub_scores": r["sub_scores"],
            })
            if r["score"] != before:
                log(f"   rescored on model JD read: {before} -> {r['score']} "
                    f"({j['title'][:40]})")

        # Render an actual tailored resume file (reorder-only, never
        # fabricated) so the semi-assisted apply flow has something real
        # to attach.
        tailored_resume = tailor_mod.build_tailored_resume(
            master_resume, j["tailored"], jd_text=j.get("description", ""))
        folder = resumes_dir / f"{_safe(j['company'])}_{_safe(j['title'])}"
        folder.mkdir(parents=True, exist_ok=True)
        docx_path = folder / "Mehul_Agarwal.docx"
        pdf_path = folder / "Mehul_Agarwal.pdf"
        try:
            resume_render.build_docx(tailored_resume, str(docx_path))
            resume_render.build_pdf(tailored_resume, str(pdf_path))
            j["resume_docx"] = str(docx_path)
            j["resume_pdf"] = str(pdf_path)
            j["change_log"] = tailor_mod.change_log(master_resume,
                                                    tailored_resume)
            # 2-page guard: tailoring only rewords/reorders so length should
            # hold, but verify the rendered PDF rather than assume.
            with pdfplumber.open(str(pdf_path)) as _pdf:
                if len(_pdf.pages) > 2:
                    log(f"   WARNING: tailored resume for '{j['title']}' "
                        f"is {len(_pdf.pages)} pages (max 2) — review it")
                    j["change_log"] += "; WARNING: exceeds 2 pages"
        except Exception as e:
            log(f"   resume render failed for '{j['title']}' ({e})")
            j["resume_docx"] = j["resume_pdf"] = ""

        log(f"   tailored {i}/{len(to_tailor)}: {j['title']} @ {j['company']}")
        time.sleep(3.0)  # gentle on free-tier rate limits

    csv_path = report.write_queue(new_jobs)
    log(f"   wrote {csv_path}")

    notify.send_digest(new_jobs, config, log=log)

    # Follow-up nudges daily; weekly stats digest on Sundays.
    tracker.send_followups(config, log=log)
    if date.today().weekday() == 6:
        tracker.send_weekly_stats(config, log=log)

    dedupe.save_seen(dedupe.mark_seen(new_jobs, seen))
    log(f"   seen-store updated ({len(seen)} total entries)")
    log("DONE. Review the CSV, use the tailored material, and submit "
        "applications yourself — that step is intentionally human.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
