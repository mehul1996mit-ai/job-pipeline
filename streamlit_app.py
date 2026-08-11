"""Streamlit UI for the job pipeline.

Run locally:   streamlit run streamlit_app.py
Host for free: share.streamlit.io -> this repo -> streamlit_app.py
               (add API keys under App settings -> Secrets)

The UI reviews queues, rebuilds tailored resume files, edits filters, and
triggers on-demand runs. Submitting applications remains a human action —
see the design boundary in README.md.
"""
import glob
import io
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

import resume_render
import tailor as tailor_mod

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

st.set_page_config(page_title="Job Pipeline — Mehul", page_icon="🎯",
                   layout="wide")
st.title("🎯 Job Pipeline")
st.caption("Daily scan → fit-scored queue → JD-tailored resumes. "
           "You review and submit — the tool never applies for you.")


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def secret_or_env(name):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, "")


def apply_bridge_markdown(url: str, config: dict) -> str:
    """Markdown for the semi-assisted-apply link: opens `url` with a
    ?jtApply=1 marker the CV Match Copilot extension's content script
    notices (see config.yaml's apply_bridge comment) and an honest badge
    saying what "Apply" here actually does: auto-fill directly, hop through
    Adzuna to the real employer page first (2026-08-01: the extension
    auto-clicks Adzuna's own "apply-capture-skip" link, then auto-fills
    there if THAT host is covered), or just open the posting like a plain
    link.
    """
    bridge = (config or {}).get("apply_bridge") or {}
    if not bridge.get("enabled", True):
        return f"[Open job posting]({url})"
    sep = "&" if "?" in url else "?"
    apply_url = f"{url}{sep}jtApply=1"
    host = urllib.parse.urlparse(url).netloc.lower()
    autofill_hosts = bridge.get("autofill_hosts") or []
    covered = any(host == h or host.endswith("." + h) for h in autofill_hosts)
    is_adzuna = host.endswith("adzuna.in") or host.endswith("adzuna.com") \
        or host.endswith("adzuna.co.in")
    if covered:
        badge = " — 🤖 extension will auto-fill on this host"
    elif is_adzuna:
        badge = (" — 🤖 extension will follow through to the real employer "
                  "page first, then auto-fill there if that host is covered")
    else:
        badge = " — opens only; this host isn't in the extension's autofill list yet"
    return f"[Open job posting]({url})  ·  [**Apply →**]({apply_url}){badge}"


# ------------------------------------------------------ data-freshness banner
# WHY THIS EXISTS (2026-08-09). The pipeline runs in GitHub Actions and
# commits each day's queue back to origin/main; this dashboard reads LOCAL
# files. Nothing used to pull, so the clone drifted silently — and twice
# (2026-08-05 and 2026-08-09) a clone sitting 4 days behind was reported as
# "the pipeline stopped running" when Actions had actually succeeded every
# single day. A stale clone and a dead pipeline looked identical from here.
# They must never look identical again, so the age of the data is stated
# outright on every page instead of being inferred from an empty-looking
# queue. dashboard_watchdog.ps1 now fast-forwards the clone every 5 minutes;
# this reports whether that is actually working rather than assuming it is.
def freshness_banner():
    from datetime import date as _fd, datetime as _fdt
    queues = sorted(glob.glob(str(DATA / "job_queue_*.csv")))
    if not queues:
        st.error("**No queue data in this clone at all.** Nothing has been "
                 "pulled or generated yet — check the pipeline on GitHub "
                 "Actions before assuming the scan is broken.")
        return

    newest = Path(queues[-1]).stem.replace("job_queue_", "")
    try:
        age = (_fd.today() - _fd.fromisoformat(newest)).days
    except ValueError:
        return

    # Read what the watchdog's last sync attempt actually did. Its absence is
    # itself informative — it means the watchdog isn't running this code yet.
    sync_line, sync_age_min = "", None
    try:
        # utf-8-sig, not utf-8: Windows PowerShell 5.1's `Set-Content
        # -Encoding utf8` always prepends a BOM, and a leading ﻿ makes
        # datetime.fromisoformat() raise — which silently cost the "N min
        # ago" heartbeat reading (caught 2026-08-09). Same reason the queue
        # CSVs are read utf-8-sig throughout this file.
        raw = (ROOT / "dashboard_sync.log").read_text(
            encoding="utf-8-sig").strip()
        stamp, _, detail = raw.partition("  ")
        try:
            sync_age_min = int((_fdt.now().astimezone()
                                - _fdt.fromisoformat(stamp)).total_seconds() // 60)
            sync_line = f"Last auto-sync check: {sync_age_min} min ago — {detail}"
        except ValueError:
            sync_line = f"Last auto-sync check: {detail}"
    except OSError:
        # No log file. On Streamlit Community Cloud that is simply normal —
        # it deploys from the repo itself, so it is current by construction
        # and there is no local clone to sync. Only worth flagging when the
        # data is ALSO old, which the age check below already handles.
        sync_line = ("" if age <= 1 else
                     "No auto-sync log — if this is the local dashboard, "
                     "dashboard_watchdog.ps1 has not run since the sync step "
                     "was added.")

    # The watchdog runs every 5 min. If its own heartbeat is older than ~20,
    # the thing keeping this clone current is itself down — which is exactly
    # the failure that must not look healthy.
    stale_watchdog = sync_age_min is not None and sync_age_min > 20
    if stale_watchdog:
        sync_line += ("  ⚠️ auto-sync watchdog looks STOPPED (expected every "
                      "5 min) — check the JobPipelineDashboardWatchdog task.")
    blocked = ("BLOCKED" in sync_line or "ERROR" in sync_line
               or stale_watchdog)
    # age 0 = today's scan already landed; 1 = normal before today's run
    # commits (it lands mid-morning IST). 2+ means something is genuinely off.
    if age <= 1 and not blocked:
        st.caption(f"🟢 Newest queue: **{newest}** "
                   f"({'today' if age == 0 else 'yesterday'}). {sync_line}")
    else:
        # Lead with whichever thing is ACTUALLY wrong. Data can be current
        # while the syncer is dead (and vice versa); a banner that headlines
        # "0 days old" on a watchdog fault sends you looking in the wrong
        # place, which is most of what made the original problem expensive.
        if age >= 2:
            headline = (f"Newest queue in this clone is {newest} — "
                        f"{age} days old.")
        else:
            headline = (f"Queue data looks current ({newest}), but the "
                        f"auto-sync that keeps it that way is unhealthy — "
                        f"it will go stale without warning.")
        st.error(
            f"⚠️ **{headline}**\n\n"
            f"{sync_line}\n\n"
            "**This does not by itself mean the pipeline failed.** It runs in "
            "GitHub Actions, not on this machine — confirm there first:\n"
            "- Runs: https://github.com/mehul1996mit-ai/job-pipeline/actions\n"
            "- Always-current dashboard: https://job-1357.streamlit.app/\n\n"
            "If Actions shows successful runs, only this local copy is behind: "
            "run `git -C C:\\Claude\\job_pipeline merge --ff-only origin/main`. "
            "If that refuses, you have local commits or unsaved queue edits "
            "blocking it — resolve them by hand so no ratings are lost.")


freshness_banner()


# CV Match Copilot (Gemini)'s Chrome extension ID — needed to reach its
# externally_connectable listener (background.js's jt.queryTracker handler,
# added 2026-08-02) from this page's own JS. It's the extension's real
# chrome://extensions ID for this dev/unpacked install, not a store slug —
# re-check it here if the extension is ever reinstalled or repackaged.
EXTENSION_ID = "ngokbgjnigblebajkjihiapolkpllhki"

tab_queue, tab_status, tab_learn, tab_run, tab_filters, tab_outreach, tab_interview = st.tabs(
    ["📋 Review queue", "🧭 Status", "🧠 Learning", "🚀 Run now", "⚙️ Filters",
     "📤 Outreach review", "🗂️ Interview Prep"])

# ------------------------------------------------------------ Review queue
with tab_queue:
    queues = sorted(glob.glob(str(DATA / "job_queue_*.csv")), reverse=True)
    if not queues:
        st.info("No queue CSVs yet. Trigger a run from the 'Run now' tab, "
                "or wait for the daily 08:30 IST scan.")
    else:
        # Date picker rather than a flat filename list: this grows by one
        # entry a day, so a dropdown stops being usable within a couple of
        # months. Only days that actually have a queue are selectable.
        from datetime import date as _d
        by_day = {Path(p).stem.replace("job_queue_", ""): p for p in queues}
        available = sorted(by_day, reverse=True)
        dc1, dc2 = st.columns([1, 2])
        picked = dc1.date_input(
            "Queue date", value=_d.fromisoformat(available[0]),
            min_value=_d.fromisoformat(available[-1]),
            max_value=_d.fromisoformat(available[0]),
            help="Every daily queue is kept indefinitely — pick any past day.")
        key = picked.isoformat()
        if key not in by_day:
            dc2.warning(
                f"No scan stored for {key}. Nearest available: "
                f"{min(available, key=lambda d: abs((_d.fromisoformat(d) - picked).days))}."
                "  \nDays are missing between 2026-07-18 and 2026-07-26 — the "
                "commit step was silently failing then (fixed 2026-07-27).")
            key = min(available,
                      key=lambda d: abs((_d.fromisoformat(d) - picked).days))
        dc2.caption(f"Showing **{key}** — {len(available)} day(s) stored, "
                    f"{available[-1]} to {available[0]}.")
        chosen = by_day[key]

        df = pd.read_csv(chosen, encoding="utf-8-sig",
                         keep_default_na=False)
        if "match_feedback" not in df.columns:
            df["match_feedback"] = ""
        df = df.sort_values("score", ascending=False).reset_index(drop=True)
        tailored_df = df[df["tailored_summary"] != ""]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("New jobs", len(df))
        c2.metric("Tailored (score ≥ floor)", len(tailored_df))
        c3.metric("Marked applied", int((df["applied"] == "yes").sum()))
        c4.metric("Rated", int((df["match_feedback"] != "").sum()))

        st.subheader("Full queue")
        show_n = st.radio("Show", [50, 100, "All"], index=0, horizontal=True,
                          help="Ranked by fit score, highest first.")
        st.caption("**match** is your relevance rating — good / partial / no. "
                   "It trains the search (see the Learning tab); it is not the "
                   "same as **applied**, which tracks what you actually "
                   "submitted. Status flow: no → yes → response / interview / "
                   "rejected / offer.")
        if "applied_on" not in df.columns:
            df["applied_on"] = ""
        # Scoring columns added 2026-07-28. Queues written before that don't
        # have them, so only show the ones this file actually carries.
        score_cols = [c for c in ("percentile", "band", "must_coverage")
                      if c in df.columns]
        view_cols = (["applied", "match_feedback", "score"] + score_cols
                     + ["title", "company", "location", "source", "url"])
        view_df = df if show_n == "All" else df.head(int(show_n))
        edited = st.data_editor(
            view_df[view_cols],
            column_config={
                "applied": st.column_config.SelectboxColumn(
                    "applied",
                    options=["no", "yes", "skip", "response", "interview",
                             "rejected", "offer"],
                    width="small"),
                "match_feedback": st.column_config.SelectboxColumn(
                    "match", options=["", "good", "partial", "no"],
                    width="small",
                    help="Was this a relevant job for you? Your ratings train "
                         "the search — the Learning tab proposes changes once "
                         "there are enough of them."),
                "url": st.column_config.LinkColumn("link"),
                "score": st.column_config.NumberColumn(
                    "fit", width="small",
                    help="Structured fit score 0-100: skill match by evidence "
                         "tier, experience, education gate, domain, "
                         "achievement density, trajectory — minus penalties."),
                "percentile": st.column_config.NumberColumn(
                    "pct", width="small",
                    help="Fit percentile calibrated against how demanding THIS "
                         "posting is, so scores compare across jobs. Modelled "
                         "comparability, not measured applicant data."),
                "must_coverage": st.column_config.TextColumn(
                    "must", width="small",
                    help="How many of the posting's must-have requirements "
                         "your CV evidences."),
            },
            disabled=[c for c in view_cols
                      if c not in ("applied", "match_feedback")],
            hide_index=True, use_container_width=True, height=520)
        if st.button("💾 Save ratings & applied-status changes"):
            from datetime import date as _date
            # `edited` covers only the visible slice; write back by index so
            # a 50-row view never blanks the rows below it.
            idx = edited.index
            newly = (df.loc[idx, "applied"].isin(["no", "skip"])
                     & ~edited["applied"].isin(["no", "skip"]))
            df.loc[idx[newly], "applied_on"] = _date.today().isoformat()
            df.loc[idx, "applied"] = edited["applied"]
            df.loc[idx, "match_feedback"] = edited["match_feedback"]
            df.to_csv(chosen, index=False, encoding="utf-8-sig")
            st.success(f"Saved {int((edited['match_feedback'] != '').sum())} "
                       f"rating(s). Commit/push the CSV so the cloud run sees "
                       f"them.")

        st.subheader("📈 Stats so far")
        import tracker as tracker_mod
        st.text(tracker_mod.weekly_stats(
            tracker_mod.load_queue_rows(str(DATA))))

        st.subheader("Tailored matches — detail & resume files")
        master = json.loads(
            (ROOT / "resume_master.json").read_text(encoding="utf-8"))
        bridge_config = load_config()
        for _, row in tailored_df.iterrows():
            channel = row.get("apply_channel", "")
            badge = " 🎯direct-apply" if channel == "direct" else ""
            with st.expander(
                    f"⭐ {row['score']} — {row['title']} @ {row['company']} "
                    f"({row['location']}){badge}"):
                st.markdown(apply_bridge_markdown(row["url"], bridge_config)
                            + (" — **direct employer ATS** (preferred: no "
                               "aggregator hop)" if channel == "direct"
                               else ""))
                st.markdown(f"**Tailored summary:** "
                            f"{row['tailored_summary']}")

                fe = json.loads(row.get("fit_exact") or "[]")
                fp = json.loads(row.get("fit_partial") or "[]")
                fg = json.loads(row.get("fit_gaps") or "[]")
                if fe or fp or fg:
                    fc1, fc2, fc3 = st.columns(3)
                    with fc1:
                        st.markdown("**✅ Exact match**")
                        for x in fe:
                            st.markdown(f"- {x}")
                    with fc2:
                        st.markdown("**🟡 Partial**")
                        for x in fp:
                            st.markdown(f"- {x}")
                    with fc3:
                        st.markdown("**❌ Gap**")
                        for x in fg:
                            st.markdown(f"- {x}")

                cl = row.get("change_log", "")
                if cl:
                    st.info(f"**Changes vs base CV:** {cl}")
                outreach = row.get("outreach_note", "")
                if outreach:
                    st.markdown("**Recruiter outreach draft** "
                                "(copy, personalize the name, send it "
                                "yourself):")
                    st.code(outreach, language=None)
                gap = row.get("honest_gap_note", "")
                if gap:
                    st.warning(f"Honest gap note: {gap}")
                rewrites = json.loads(row.get("rewritten_bullets") or "[]")
                if rewrites:
                    st.markdown("**JD-aligned rewording** "
                                "(validated — metrics unchanged):")
                    for rw in rewrites:
                        if tailor_mod.rewrite_is_safe(
                                rw.get("original", ""),
                                rw.get("rewritten", "")):
                            st.markdown(f"- {rw['rewritten']}")

                fields = {
                    "tailored_summary": row["tailored_summary"],
                    "bullets_to_lead_with":
                        json.loads(row.get("bullets_to_lead_with") or "[]"),
                    "rewritten_bullets": rewrites,
                    "keywords_to_add_if_true":
                        json.loads(row.get("keywords_to_add_if_true")
                                   or "[]"),
                }
                resume = tailor_mod.build_tailored_resume(
                    master, fields,
                    jd_text=row.get("description_snippet", ""))
                docx_buf, pdf_buf = io.BytesIO(), io.BytesIO()
                try:
                    resume_render.build_docx(resume, docx_buf)
                    resume_render.build_pdf(resume, pdf_buf)
                    d1, d2 = st.columns(2)
                    d1.download_button(
                        "⬇️ Tailored resume (DOCX)", docx_buf.getvalue(),
                        file_name="Mehul_Agarwal.docx",
                        key=f"docx{row['url']}")
                    d2.download_button(
                        "⬇️ Tailored resume (PDF)", pdf_buf.getvalue(),
                        file_name="Mehul_Agarwal.pdf",
                        key=f"pdf{row['url']}")
                except Exception as e:
                    st.error(f"Resume render failed: {e}")

        st.subheader("Tailor any other job on demand")
        untailored = df[df["tailored_summary"] == ""]
        if untailored.empty:
            st.caption("Every job in this queue is already tailored.")
        elif not secret_or_env("GEMINI_API_KEY"):
            st.caption("Set GEMINI_API_KEY (secrets or environment) to "
                       "enable on-demand tailoring.")
        else:
            options = {
                f"[{r['score']}] {r['title']} @ {r['company']} "
                f"({r['location']})": idx
                for idx, r in untailored.iterrows()}
            pick = st.selectbox("Job (below the auto-tailor score floor, "
                                "or beyond the daily top-N)",
                                list(options))
            if st.button("🎯 Tailor this job now"):
                os.environ["GEMINI_API_KEY"] = secret_or_env(
                    "GEMINI_API_KEY")
                idx = options[pick]
                row = df.loc[idx]
                import matcher
                from cv_parser import parse_cv
                cv = parse_cv(ROOT / "base_cv.pdf")
                job = {"title": row["title"], "company": row["company"],
                       "description": row.get("description_snippet", "")}
                with st.spinner("Tailoring..."):
                    t = tailor_mod.tailor_job(
                        cv.raw_text, job,
                        matcher.matched_keywords(job["description"],
                                                 cv.keywords),
                        load_config(), log=st.write)
                if not t.get("tailored_summary"):
                    st.error("Tailoring returned nothing usable — "
                             f"note: {t.get('honest_gap_note', '')}")
                else:
                    fit = t.get("fit_analysis") or {}
                    df.loc[idx, "tailored_summary"] = t["tailored_summary"]
                    df.loc[idx, "bullets_to_lead_with"] = json.dumps(
                        t.get("bullets_to_lead_with", []),
                        ensure_ascii=False)
                    df.loc[idx, "rewritten_bullets"] = json.dumps(
                        t.get("rewritten_bullets", []), ensure_ascii=False)
                    df.loc[idx, "keywords_to_add_if_true"] = json.dumps(
                        t.get("keywords_to_add_if_true", []),
                        ensure_ascii=False)
                    df.loc[idx, "fit_exact"] = json.dumps(
                        fit.get("exact_matches", []), ensure_ascii=False)
                    df.loc[idx, "fit_partial"] = json.dumps(
                        fit.get("partial_matches", []), ensure_ascii=False)
                    df.loc[idx, "fit_gaps"] = json.dumps(
                        fit.get("gaps", []), ensure_ascii=False)
                    df.loc[idx, "outreach_note"] = t.get("outreach_note",
                                                         "")
                    df.loc[idx, "honest_gap_note"] = t.get(
                        "honest_gap_note", "")
                    resume = tailor_mod.build_tailored_resume(
                        master, t, jd_text=job["description"])
                    df.loc[idx, "change_log"] = tailor_mod.change_log(
                        master, resume)
                    df.to_csv(chosen, index=False, encoding="utf-8-sig")
                    st.success("Tailored and saved to the queue — "
                               "reloading...")
                    st.rerun()

# ------------------------------------------------------------------- Status
# Merges PIPELINE-side stages (found/scored/tailored — from this CSV) with
# BROWSER-side stages (opened/tailored/filled/uncertain/submitted — live from
# the CV Match Copilot extension's own tracker) into one per-job checklist.
#
# The browser-side half is read entirely client-side by static/jt_status.html
# via chrome.runtime.sendMessage against the extension's read-only
# jt.queryTracker listener. That file is served through Streamlit's static
# file server (.streamlit/config.toml's enableStaticServing) rather than
# st.components.v1.html: html() renders into a sandboxed srcdoc iframe with
# an OPAQUE origin (verified live 2026-08-02 — allow-same-origin does not
# fix this for srcdoc), which the extension's externally_connectable can
# never match. A static file served at /app/static/... shares this page's
# real origin, which does match. Python only writes the job list each
# render; it never sees the extension's tracker data — no server round trip
# for it, and no path for this page to WRITE anything back into the
# extension's storage.
with tab_status:
    queues = sorted(glob.glob(str(DATA / "job_queue_*.csv")), reverse=True)
    if not queues:
        st.info("No queue CSVs yet.")
    else:
        from datetime import date as _d2
        status_by_day = {Path(p).stem.replace("job_queue_", ""): p for p in queues}
        status_available = sorted(status_by_day, reverse=True)
        status_picked = st.date_input(
            "Queue date", value=_d2.fromisoformat(status_available[0]),
            min_value=_d2.fromisoformat(status_available[-1]),
            max_value=_d2.fromisoformat(status_available[0]),
            key="status_date",
            help="Applications keep moving through their steps after the day "
                 "they were tailored — pick any past day to see that day's "
                 "queue, still checked against today's live browser state.")
        status_key = status_picked.isoformat()
        if status_key not in status_by_day:
            status_key = min(status_available,
                              key=lambda dd: abs((_d2.fromisoformat(dd) - status_picked).days))
        status_chosen = status_by_day[status_key]

        status_df = pd.read_csv(status_chosen, encoding="utf-8-sig",
                                 keep_default_na=False)
        status_tailored = status_df[status_df["tailored_summary"] != ""]
        st.caption(f"Showing **{status_key}** — {len(status_tailored)} tailored "
                   f"job(s), {len(status_available)} day(s) stored. Pipeline "
                   f"stage comes from this day's CSV; browser stage is read "
                   f"live from the CV Match Copilot extension in *your* "
                   f"browser — it will only show data if this page is open "
                   f"in the Chrome profile that has the extension installed.")

        static_dir = ROOT / "static"
        static_dir.mkdir(exist_ok=True)
        (static_dir / "jt_status_data.json").write_text(json.dumps({
            "jobs": [
                {
                    "url": r["url"],
                    "title": r.get("title", ""),
                    "company": r.get("company", ""),
                    "score": r.get("score", ""),
                }
                for _, r in status_tailored.iterrows()
            ]
        }, ensure_ascii=False), encoding="utf-8")

        import streamlit.components.v1 as components
        components.iframe(
            # Cache-bust on the selected day: the iframe's src is otherwise
            # identical across reruns, so switching dates would rewrite
            # jt_status_data.json server-side but the already-loaded iframe
            # would never re-fetch it (Streamlit reuses the DOM node when
            # src is unchanged).
            src=f"/app/static/jt_status.html?d={status_key}",
            height=min(120 + 40 * max(len(status_tailored), 1), 700),
            scrolling=True)

# ----------------------------------------------------------------- Learning
with tab_learn:
    import feedback as feedback_mod

    learn_cfg = load_config()

    st.markdown(
        "Rate jobs **good / partial / no** in the review queue. Once there "
        "are enough ratings, this proposes changes to your search and "
        "scoring — which you accept or ignore. **Nothing is applied "
        "automatically**: a scorer that quietly re-tunes itself makes your "
        "own score history stop meaning anything.")

    prop = feedback_mod.build_proposal(learn_cfg, str(DATA))
    rd = prop["readiness"]
    cts = rd["counts"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rated", rd["total"])
    m2.metric("good", cts["good"])
    m3.metric("partial", cts["partial"])
    m4.metric("no", cts["no"])
    st.progress(min(1.0, rd["total"] / feedback_mod.MIN_LABELS))
    (st.success if rd["ready"] else st.info)(rd["note"])

    sep = prop["separation"]
    if sep.get("separates") is not None:
        st.subheader("Is the score actually working?")
        st.caption("Checked before anything is tuned on top of it — if the "
                   "score can't tell your good matches from your bad ones, "
                   "re-weighting its parts won't help.")
        (st.success if sep["separates"] else st.warning)(sep["note"])

    if prop["keywords"]:
        st.subheader("Title keywords by hit rate")
        st.caption("Worst first. A keyword you almost always reject is "
                   "pulling the search in the wrong direction.")
        st.dataframe(pd.DataFrame(prop["keywords"]), hide_index=True,
                     use_container_width=True)
    if prop["sources"]:
        st.subheader("Sources by hit rate")
        st.dataframe(pd.DataFrame(prop["sources"]), hide_index=True,
                     use_container_width=True)

    if rd["ready"]:
        st.subheader("Proposed changes")
        drops = prop["suggested_drops"]
        w = prop["weights"] or {}
        if drops:
            st.write("**Title keywords to drop** — you rated 70%+ of their "
                     "jobs 'no':")
            st.code("\n".join(f"- {d}" for d in drops))
        if w.get("proposed"):
            comp = pd.DataFrame({
                "sub-score": list(w["proposed"]),
                "current": [((learn_cfg.get("scoring") or {}).get("weights") or {}
                             ).get(k) for k in w["proposed"]],
                "proposed": list(w["proposed"].values()),
                "correlation with 'good'": [w["correlations"].get(k)
                                            for k in w["proposed"]],
            })
            st.dataframe(comp, hide_index=True, use_container_width=True)
            st.caption(w["note"])
        if drops or w.get("proposed"):
            st.warning("Review these before accepting. Applying them "
                       "rewrites config.yaml and changes how every future "
                       "job scores — past scores stay as they were.")
            if st.button("✅ Accept and write to config.yaml"):
                if drops:
                    learn_cfg["filters"]["title_keywords"] = [
                        k for k in learn_cfg["filters"]["title_keywords"]
                        if k not in drops]
                if w.get("proposed"):
                    learn_cfg.setdefault("scoring", {})["weights"] = w["proposed"]
                (ROOT / "config.yaml").write_text(
                    yaml.safe_dump(learn_cfg, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
                st.success("config.yaml updated. Commit/push it so the daily "
                           "cloud run picks it up.")
        else:
            st.info("Nothing worth changing — your current config is "
                    "consistent with how you've been rating jobs.")

# ------------------------------------------------------------------ Run now
with tab_run:
    st.markdown(
        "Runs the full pipeline (scan → score → tailor → digest) right "
        "here. The daily 08:30 IST GitHub Actions run continues "
        "regardless.")
    have = {k: bool(secret_or_env(k)) for k in
            ["ADZUNA_APP_ID", "ADZUNA_APP_KEY", "GEMINI_API_KEY",
             "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]}
    st.write({k: ("✅ set" if v else "— missing (that source/step will be "
                  "skipped)") for k, v in have.items()})
    if st.button("🚀 Run pipeline now", type="primary"):
        env = dict(os.environ)
        for k in have:
            v = secret_or_env(k)
            if v:
                env[k] = v
        box = st.empty()
        lines = []
        with st.spinner("Running… (2–5 minutes)"):
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "main.py")],
                cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace")
            for line in proc.stdout:
                lines.append(line.rstrip())
                box.code("\n".join(lines[-25:]))
            proc.wait()
        if proc.returncode == 0:
            st.success("Run complete — open the 'Review queue' tab.")
        else:
            st.error(f"Run failed (exit {proc.returncode}) — see log above.")

# ------------------------------------------------------------------ Filters
with tab_filters:
    st.markdown("Edits `config.yaml`. Locally this persists; on Streamlit "
                "Cloud it lasts until redeploy — commit lasting changes to "
                "the repo.")
    cfg = load_config()
    f = cfg["filters"]
    title_kw = st.text_area("Title keywords (one per line — a job needs "
                            "ANY of these in its title)",
                            "\n".join(f.get("title_keywords", [])))
    cities = st.text_area("Cities allowlist (one per line; empty = all; "
                          "'remote' always passes)",
                          "\n".join(f.get("cities", [])))
    col1, col2, col3 = st.columns(3)
    min_salary = col1.number_input(
        "Min annual salary (INR; 0 = disabled; enforced only when the "
        "listing reports pay)", 0, 20000000,
        int(f.get("min_salary_annual") or 0), step=100000)
    score_floor = col2.number_input(
        "Min fit score to tailor (0–100)", 0, 100,
        int(f.get("min_score_to_tailor", 55)))
    exp_years = col3.number_input(
        "My experience (years)", 0, 40,
        int(cfg["profile"].get("experience_years", 4)))
    top_n = st.slider("Tailor top N per run", 5, 50,
                      int(cfg["tailor"].get("top_n", 25)))
    remote_only = st.checkbox("Remote-only (drop all on-site/hybrid "
                              "listings)", value=bool(f.get("remote_only")))
    if st.button("💾 Save filters"):
        f["title_keywords"] = [s.strip() for s in title_kw.splitlines()
                               if s.strip()]
        f["cities"] = [s.strip() for s in cities.splitlines() if s.strip()]
        f["min_salary_annual"] = min_salary or None
        f["min_score_to_tailor"] = score_floor
        f["remote_only"] = remote_only
        cfg["profile"]["experience_years"] = exp_years
        cfg["tailor"]["top_n"] = top_n
        with open(ROOT / "config.yaml", "w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg, fh, sort_keys=False, allow_unicode=True)
        st.success("config.yaml updated.")

# ---------------------------------------------------------- Outreach review
with tab_outreach:
    st.caption(
        "Career Agent (A8/A9) outreach drafts. Every send here is one explicit "
        "click by you on the exact draft shown below — nothing sends on its "
        "own. Full auto-send was asked for and declined; see CLAUDE.md's "
        "2026-08-10 entry for why."
    )
    try:
        import outreach_store as ca_store
        import outreach_crm as ca_crm
        import outreach_send as ca_send
    except ImportError as e:
        st.info(f"Career Agent modules not available ({e}).")
    else:
        if not os.path.exists(ca_store.DB_PATH):
            st.info("No career_agent.sqlite3 yet — nothing to review. Run "
                     "company_targeting.py / authority_graph.py / outreach.py first.")
        else:
            with ca_store.connect() as _conn:
                pending = ca_send.list_pending_review(_conn)
            if not pending:
                st.success("No drafts waiting for review.")
            else:
                st.write(f"**{len(pending)} draft(s) waiting for review.**")
                for row in pending:
                    label = (f"{row['company_name']} — "
                              f"{row.get('person_name') or 'unknown contact'} — {row['subject']}")
                    with st.expander(label):
                        st.write(f"**To:** {row.get('to_email') or '(unknown)'}")
                        st.write(f"**Subject:** {row['subject']}")
                        st.text_area("Body", row["body"], height=150,
                                     key=f"body_{row['id']}", disabled=True)
                        c1, c2 = st.columns(2)
                        if c1.button("✅ Approve & send", key=f"approve_{row['id']}"):
                            try:
                                import gmail_auth as ca_gmail_auth
                                service = ca_gmail_auth.get_service()
                            except Exception as e:
                                st.error(f"Gmail auth failed: {e}")
                            else:
                                try:
                                    with ca_store.connect() as _conn2:
                                        ca_send.send_approved_draft(
                                            _conn2, service, row["id"], confirmed=True)
                                    st.success("Sent.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Send failed: {e}")
                        if c2.button("🚫 Reject", key=f"reject_{row['id']}"):
                            with ca_store.connect() as _conn2:
                                ca_crm.update_outreach_state(
                                    _conn2, row["id"], "CLOSED", reason="rejected_at_review")
                            st.info("Rejected — moved to CLOSED, not sent.")

                            st.rerun()

# --------------------------------------------------------------- Interview Prep
with tab_interview:
    import interview_ui
    interview_ui.render()
