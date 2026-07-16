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


tab_queue, tab_run, tab_filters = st.tabs(
    ["📋 Review queue", "🚀 Run now", "⚙️ Filters"])

# ------------------------------------------------------------ Review queue
with tab_queue:
    queues = sorted(glob.glob(str(DATA / "job_queue_*.csv")), reverse=True)
    if not queues:
        st.info("No queue CSVs yet. Trigger a run from the 'Run now' tab, "
                "or wait for the daily 08:30 IST scan.")
    else:
        chosen = st.selectbox(
            "Queue date", queues,
            format_func=lambda p: Path(p).stem.replace("job_queue_", ""))
        df = pd.read_csv(chosen, encoding="utf-8-sig",
                         keep_default_na=False)
        tailored_df = df[df["tailored_summary"] != ""]
        c1, c2, c3 = st.columns(3)
        c1.metric("New jobs", len(df))
        c2.metric("Tailored (score ≥ floor)", len(tailored_df))
        c3.metric("Marked applied", int((df["applied"] == "yes").sum()))

        st.subheader("Full queue")
        st.caption("Status flow: no → yes (applied) → response / interview "
                   "/ rejected / offer. Plain 'yes' rows with no outcome "
                   "get a Telegram follow-up nudge after 7 days.")
        if "applied_on" not in df.columns:
            df["applied_on"] = ""
        edited = st.data_editor(
            df[["applied", "score", "title", "company", "location",
                "source", "url"]],
            column_config={
                "applied": st.column_config.SelectboxColumn(
                    "applied",
                    options=["no", "yes", "skip", "response", "interview",
                             "rejected", "offer"],
                    width="small"),
                "url": st.column_config.LinkColumn("link"),
                "score": st.column_config.NumberColumn(width="small"),
            },
            disabled=["score", "title", "company", "location", "source",
                      "url"],
            hide_index=True, use_container_width=True, height=380)
        if st.button("💾 Save applied-status changes"):
            from datetime import date as _date
            newly_applied = (df["applied"].isin(["no", "skip"])
                             & ~edited["applied"].isin(["no", "skip"]))
            df.loc[newly_applied, "applied_on"] = _date.today().isoformat()
            df["applied"] = edited["applied"]
            df.to_csv(chosen, index=False, encoding="utf-8-sig")
            st.success("Saved. Commit/push the CSV if you want the cloud "
                       "follow-up nudges to see these statuses.")

        st.subheader("📈 Stats so far")
        import tracker as tracker_mod
        st.text(tracker_mod.weekly_stats(
            tracker_mod.load_queue_rows(str(DATA))))

        st.subheader("Tailored matches — detail & resume files")
        master = json.loads(
            (ROOT / "resume_master.json").read_text(encoding="utf-8"))
        for _, row in tailored_df.iterrows():
            channel = row.get("apply_channel", "")
            badge = " 🎯direct-apply" if channel == "direct" else ""
            with st.expander(
                    f"⭐ {row['score']} — {row['title']} @ {row['company']} "
                    f"({row['location']}){badge}"):
                st.markdown(f"[Open job posting]({row['url']})"
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
