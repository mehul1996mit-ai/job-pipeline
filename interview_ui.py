"""Streamlit UI for the Interview Prep toolkit — extends streamlit_app.py
(one new tab calls render() from here) rather than forking the app, per the
UI master prompt's implementation-reality constraint.

Scope: only screens backed by real, built subsystems. Practice, Readiness,
and Interview Day all depend on Phase 2 (evaluation engine T§5, mastery T§8,
readiness T§9, mock interview T§10) — none of that exists yet, so those
screens are NOT built here; a plain note explains why rather than shipping
a screen with no real data behind it. Company/segment research (T§11) is
the same story. What IS built: the process switcher (T§14) and the full
🎯 Prepare surface (E§4) — read-through queue, answer editor with the
fill-in-blank device, fact review queue (E§6) — reorganized into five
role-scoped tabs (Overview / Resume Claims / Question Bank / Story Bank /
Fact Review) so a single long scrolling page doesn't stand in for real
navigation.

Token system (§1 of the UI master prompt) is injected once via
_inject_css(). Streamlit's text_area can't render inline styled HTML while
editable, so the compromise is: a read-only styled HTML preview (with the
underlined fill-in-blank device) above a plain-text editable textarea below
it — not fighting Streamlit's own limits, per §10's own instruction.
"""
from __future__ import annotations

import html
import re

import streamlit as st

import interview_store
import interview_prep
import interview_stories
import interview_answers
import interview_question_bank as qb


# ---------------------------------------------------------------- token CSS

def _inject_css():
    st.markdown("""
    <style>
    :root {
        --ink: #211E1B; --paper: #E9E3D6; --brass: #B08D3E;
        --redaction: #151311; --confirmed: #6E8B7C; --flag: #B5563C;
    }
    .ib-card {
        background: var(--paper); color: var(--ink);
        border-radius: 4px; padding: 1.1rem 1.3rem; margin-bottom: 0.9rem;
        border-left: 4px solid var(--brass);
        font-family: Georgia, 'Iowan Old Style', serif; line-height: 1.55;
    }
    .ib-label {
        font-family: 'Courier New', ui-monospace, monospace; font-size: 0.72rem;
        letter-spacing: 0.04em; text-transform: uppercase; opacity: 0.7;
    }
    .ib-chip {
        display: inline-block; font-family: 'Courier New', ui-monospace, monospace;
        font-size: 0.7rem; letter-spacing: 0.03em; text-transform: uppercase;
        padding: 0.15rem 0.5rem; border-radius: 3px; margin-right: 0.4rem;
        border: 1px solid currentColor;
    }
    .ib-chip-gap { color: var(--flag); }
    .ib-chip-complete { color: var(--confirmed); }
    .ib-chip-neutral { color: var(--brass); }
    .ib-chip-review-unread { color: var(--flag); }
    .ib-chip-review-skimmed { color: var(--brass); }
    .ib-chip-review-edited, .ib-chip-review-rewritten { color: var(--confirmed); }
    .ib-blank {
        display: inline-block; border-bottom: 2px solid var(--brass);
        min-width: 7em; padding: 0 0.2em; color: var(--brass);
        font-style: italic; font-size: 0.92em;
    }
    .ib-redaction {
        display: inline-block; background: var(--redaction); color: var(--redaction);
        border-radius: 2px; padding: 0 0.6em; user-select: none;
    }
    .ib-process-pill {
        display: inline-block; padding: 0.35rem 0.8rem; margin-right: 0.5rem;
        border-radius: 3px; font-family: 'Courier New', monospace; font-size: 0.8rem;
        border: 1px solid var(--brass);
    }
    .ib-progress-track {
        background: rgba(176,141,62,0.15); border-radius: 4px; height: 10px;
        overflow: hidden; margin: 0.35rem 0;
    }
    .ib-progress-fill { background: var(--brass); height: 100%; }
    .ib-focus-group { margin-bottom: 1rem; }
    .ib-focus-title { font-weight: 600; margin-bottom: 0.1rem; }
    </style>
    """, unsafe_allow_html=True)


def _render_fillin_blanks(text: str) -> str:
    """[YOU FILL: X] -> an underlined inline blank (§0's fill-in-blank
    device), for the read-only preview only — the editable textarea below
    keeps the literal bracketed token, since that's what needs to be typed
    over and HTML can't render inside an editable Streamlit widget."""
    escaped = html.escape(text or "")

    def _sub(m):
        label = html.escape(m.group(1).strip())
        return f'<span class="ib-blank" title="{label}">{label}</span>'
    return re.sub(r"\[YOU FILL:\s*([^\]]+)\]", _sub, escaped)


def _draft_status_chip(status: str) -> str:
    cls = {"complete": "ib-chip-complete", "complete_with_gaps": "ib-chip-gap",
          "insufficient_context": "ib-chip-gap"}.get(status, "ib-chip-neutral")
    return f'<span class="ib-chip {cls}">{status.replace("_", " ")}</span>'


def _review_depth_chip(depth: str) -> str:
    return f'<span class="ib-chip ib-chip-review-{depth}">{depth}</span>'


# ------------------------------------------------------------- data helpers

@st.cache_data(show_spinner=False)
def _master_resume():
    import json
    from pathlib import Path
    with open(Path(__file__).resolve().parent / "resume_master.json", encoding="utf-8") as f:
        return json.load(f)


def _ensure_db():
    interview_store.init_db()


def _ensure_candidate_model(master_resume):
    """Idempotent — build_candidate_model() itself no-ops past the first
    real build (interview_prep.py), this just guarantees it's been called
    at least once per Streamlit session."""
    if st.session_state.get("_ib_candidate_built"):
        return
    import cv_structure
    parts = [master_resume.get("summary", "")]
    for c in master_resume.get("experience", []):
        for r in c.get("roles", []):
            parts.extend(r.get("bullets", []))
    for g in master_resume.get("skills", []):
        parts.append(g.get("items", ""))
    structured = cv_structure.parse_cv_structured("\n".join(parts))
    interview_prep.build_candidate_model(master_resume, structured)
    st.session_state["_ib_candidate_built"] = True


def _list_processes(conn):
    return conn.execute(
        """SELECT id, company_name, role_title, scheduled_date FROM interview_process
           ORDER BY (scheduled_date IS NULL), scheduled_date ASC, id DESC"""
    ).fetchall()


def _days_to(date_str):
    if not date_str:
        return None
    from datetime import date, datetime
    try:
        d = datetime.fromisoformat(date_str).date() if "T" not in date_str else \
            datetime.fromisoformat(date_str).date()
        return (d - date.today()).days
    except Exception:
        return None


# --------------------------------------------------------- process switcher

def _process_switcher(conn):
    st.markdown('<div class="ib-label">Process switcher</div>', unsafe_allow_html=True)
    processes = _list_processes(conn)

    # A just-created process wants to become the active one, but Streamlit
    # forbids setting a widget's own state key (here, "ib_process_radio")
    # AFTER that widget has already been instantiated in a script run --
    # found live: setting it from the form handler below (which runs after
    # the radio() call further down) raised StreamlitAPIException outright,
    # it didn't just silently no-op. So the switch is staged as a plain,
    # always-settable marker and only resolved into the widget's real state
    # key here, before radio() is created in THIS run.
    pending_id = st.session_state.pop("_ib_pending_process_id", None)
    if pending_id is not None:
        idx = next((i for i, p in enumerate(processes) if p["id"] == pending_id), None)
        if idx is not None:
            st.session_state["ib_process_radio"] = idx

    labels = []
    for p in processes:
        days = _days_to(p["scheduled_date"])
        suffix = f"{days}d" if days is not None else "no date"
        labels.append(f"{p['company_name']} — {p['role_title']} — {suffix}")

    if processes:
        idx = st.radio("Active process", options=range(len(processes)),
                       format_func=lambda i: labels[i], horizontal=True,
                       label_visibility="collapsed",
                       key="ib_process_radio")
        active = processes[idx]
        st.session_state["ib_active_process_id"] = active["id"]
    else:
        st.info("No interview processes yet — create one below from a JD.")
        st.session_state["ib_active_process_id"] = None

    with st.expander("+ New process (paste a JD)", expanded=not processes):
        with st.form("ib_new_process_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            company = c1.text_input("Company")
            role = c2.text_input("Role title")
            jd_text = st.text_area("Job description (paste)", height=200)
            sched = st.date_input("Interview date (optional)", value=None)
            submitted = st.form_submit_button("Create process", type="primary")
        if submitted:
            if not company or not role or not jd_text.strip():
                st.error("Company, role, and JD text are all required.")
            else:
                master_resume = _master_resume()
                _ensure_candidate_model(master_resume)
                result = interview_prep.process_new_jd(
                    company, role, jd_text, "pasted", master_resume,
                    scheduled_date=str(sched) if sched else None)
                st.session_state["ib_active_process_id"] = result["process_id"]
                # Resolved into the actual "ib_process_radio" widget key at
                # the TOP of _process_switcher on the next run, before that
                # widget is instantiated -- see the comment there for why
                # setting it directly here throws (found live).
                st.session_state["_ib_pending_process_id"] = result["process_id"]
                st.success(f"Process created — {len(result['requirement_ids'])} JD "
                          f"requirements extracted, {len(result['topic_ids'])} prep "
                          "topics generated.")
                conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                               # manager only commits on normal exit, so this must
                               # happen explicitly before every rerun, or the write
                               # this button just made is silently lost.
                st.rerun()


# ------------------------------------------------------------------ fit rollup

def _fit_rollup(conn, process_id):
    """Aggregates the existing per-requirement matched/partial/gap signal
    (already computed by interview_prep.ingest_jd -> requirement_match) into
    one honest summary number. Nothing new is inferred -- this is a rollup
    of data that already exists, not a new judgment."""
    rows = conn.execute(
        """SELECT jr.tier, rm.match_status FROM jd_requirement jr
           JOIN requirement_match rm ON rm.requirement_id = jr.id
           WHERE jr.process_id = ?""", (process_id,)).fetchall()
    if not rows:
        return None
    tier_weight = {"must_have": 3.0, "preferred": 1.0, "key": 1.5}
    status_score = {"matched": 1.0, "partial": 0.5, "gap": 0.0}
    total_w = sum(tier_weight.get(r["tier"], 1.0) for r in rows)
    if total_w == 0:
        return None
    weighted = sum(tier_weight.get(r["tier"], 1.0) * status_score.get(r["match_status"], 0.0)
                  for r in rows)
    return round(100 * weighted / total_w)


# ------------------------------------------------------------ progress bar

def _progress_bar(conn, process_id):
    """Honest generated/total coverage across every question this process
    could have an answer for (claim questions + metric defense + the FULL
    base bank, not just the top-10 ranked slice) -- a genuine count, never
    a fabricated readiness/confidence score. Same discipline this repo
    already applies to the fit rollup above."""
    claim_total = conn.execute("SELECT COUNT(*) AS n FROM claim_question").fetchone()["n"]
    metric_total = conn.execute("SELECT COUNT(*) AS n FROM metric_defense").fetchone()["n"]
    base_total = len(qb.BASE_QUESTIONS)
    total = claim_total + metric_total + base_total
    if not total:
        return
    rows = conn.execute(
        """SELECT draft_status, review_depth FROM prepared_answer_version
           WHERE process_id=? AND superseded_by IS NULL""", (process_id,)).fetchall()
    generated = len(rows)
    reviewed = sum(1 for r in rows if r["review_depth"] in ("edited", "rewritten"))
    pct = round(100 * generated / total)
    st.markdown('<div class="ib-label">Preparation coverage</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="ib-progress-track"><div class="ib-progress-fill" style="width:{pct}%"></div></div>'
        f'<div style="font-size:0.85em;opacity:0.8">{generated} of {total} questions drafted '
        f'({pct}%) · {reviewed} reviewed or rewritten by you. Not a readiness score — just a '
        f'count of what exists vs. what you\'ve looked at.</div>',
        unsafe_allow_html=True)


# --------------------------------------------------------------- focus list

_FOCUS_GROUPS = [
    ("requirement_gap", "🎯 JD requirements to shore up",
     "This JD asks for it and it isn't clearly on your resume yet. Prepare it in Resume Claims or Question Bank."),
    ("high_risk_claim", "⚠️ Claims that need a strong defense",
     "Carries a number but ownership isn't crystal clear — the exact combination a follow-up exposes. Go to Resume Claims."),
    ("uncovered_competency", "📖 Competencies with no story yet",
     "Nothing in your story bank demonstrates this. Go to Story Bank."),
    ("base_question_bank", "💬 Recommended standard questions",
     "Highest-fit standard questions for this specific role. Starred ⭐ in Question Bank."),
]


def _focus_list(conn, process_id):
    topics = conn.execute(
        """SELECT topic_text, source, priority FROM prep_topic
           WHERE process_id = ? ORDER BY priority DESC""", (process_id,)).fetchall()
    if not topics:
        st.caption("No focus items yet for this process.")
        return
    grouped = {}
    for t in topics:
        grouped.setdefault(t["source"], []).append(t)

    st.markdown('<div class="ib-label">Focus list — what to prepare first</div>',
               unsafe_allow_html=True)
    any_shown = False
    for source_key, title, blurb in _FOCUS_GROUPS:
        items = grouped.get(source_key, [])
        if not items:
            continue
        any_shown = True
        st.markdown(f'<div class="ib-focus-group"><div class="ib-focus-title">{title}</div>'
                   f'<div style="font-size:0.82em;opacity:0.7;margin-bottom:0.3em">{blurb}</div>',
                   unsafe_allow_html=True)
        for t in items[:5]:
            st.markdown(f"- {html.escape(t['topic_text'])}")
        if len(items) > 5:
            st.caption(f"+ {len(items) - 5} more")
        st.markdown("</div>", unsafe_allow_html=True)
    if not any_shown:
        st.caption("No focus items yet for this process.")


# -------------------------------------------------------------- claim picker

def _claim_options(conn):
    return conn.execute(
        "SELECT id, claim_text, source_company, source_role, risk_level FROM resume_claim "
        "ORDER BY risk_level DESC").fetchall()


def _claim_questions(conn, claim_id):
    return conn.execute(
        "SELECT id, question_type, question_text FROM claim_question WHERE claim_id = ?",
        (claim_id,)).fetchall()


def _metric_defense_questions(conn, claim_id):
    return conn.execute(
        "SELECT id, dimension, question_text FROM metric_defense WHERE claim_id = ?",
        (claim_id,)).fetchall()


# -------------------------------------------------------------- generation

def _generate_for_claim(conn, process_id, claim_row, master_resume):
    """Generates BOTH the follow-up question set (T§4.4) and, if this claim
    carries a metric, the metrics-defense set (T§4.5) — a claim's full
    preparation surface, not just the question tree."""
    claim = dict(id=claim_row["id"], claim_text=claim_row["claim_text"],
                source_company=claim_row["source_company"])
    cq = [{"question_ref_id": q["id"], "question_text": q["question_text"],
          "claim": claim, "question_source": "claim_question"}
         for q in _claim_questions(conn, claim_row["id"])]
    cq += [{"question_ref_id": q["id"], "question_text": q["question_text"],
           "claim": claim, "question_source": "metric_defense"}
          for q in _metric_defense_questions(conn, claim_row["id"])]
    with st.spinner(f"Generating {len(cq)} answer drafts (live Gemini calls — this takes a bit)…"):
        results = interview_answers.generate_answer_batch(conn, process_id, cq, master_resume)
    return results


# -------------------------------------------------------------- answer editor

def _current_version_row(conn, process_id, question_source, question_ref_id):
    return conn.execute(
        """SELECT * FROM prepared_answer_version
           WHERE process_id=? AND question_source=? AND question_ref_id=?
             AND superseded_by IS NULL ORDER BY version_no DESC LIMIT 1""",
        (process_id, question_source, question_ref_id)).fetchone()


def _answer_editor(conn, process_id, question_source, question_ref_id, question_text, claim_text):
    row = _current_version_row(conn, process_id, question_source, question_ref_id)
    if not row:
        st.caption("No draft yet.")
        return

    interview_answers.mark_skimmed(conn, row["id"])

    st.markdown(f'<div class="ib-label">What this tests</div>'
               f'<div style="opacity:0.85;font-size:0.9em;margin-bottom:0.6rem">'
               f'{html.escape(claim_text or "")}</div>', unsafe_allow_html=True)

    chips = _draft_status_chip(row["draft_status"]) + _review_depth_chip(row["review_depth"])
    st.markdown(f'<div style="margin-bottom:0.4rem">{chips}</div>', unsafe_allow_html=True)

    preview_html = _render_fillin_blanks(row["body_text"])
    st.markdown(f'<div class="ib-card">{preview_html}</div>', unsafe_allow_html=True)

    edit_key = f"ib_edit_{question_source}_{row['id']}"
    edited = st.text_area("Edit this answer", value=row["body_text"], height=180, key=edit_key)

    c1, c2, c3, c4, c5 = st.columns(5)
    if c1.button("Save revision", key=f"ib_save_{question_source}_{row['id']}"):
        if edited != row["body_text"]:
            interview_answers.revise_answer(conn, process_id, question_source,
                                            question_ref_id, edited)
            fact_ids = interview_answers.detect_and_insert_fact_candidates(
                conn, process_id, row["id"], edited, claim_ref=row["claim_id"])
            st.success(f"Saved. {len(fact_ids)} fact(s) detected for review in the Fact Review tab." if fact_ids else "Saved.")
            conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                           # manager only commits on normal exit, so this must
                           # happen explicitly before every rerun, or the write
                           # this button just made is silently lost.
            st.rerun()
        else:
            st.caption("No change to save.")
    if c2.button("Regenerate", key=f"ib_regen_{question_source}_{row['id']}"):
        master_resume = _master_resume()
        claim = dict(id=row["claim_id"], claim_text=claim_text, source_company=None)
        with st.spinner("Regenerating…"):
            interview_answers.generate_answer_for_question(
                conn, process_id, question_source, question_ref_id, question_text,
                claim, master_resume)
        conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                       # manager only commits on normal exit, so this must
                       # happen explicitly before every rerun, or the write
                       # this button just made is silently lost.
        st.rerun()
    if c3.button("Critique", key=f"ib_critique_{question_source}_{row['id']}"):
        import interview_llm
        with st.spinner("Critiquing (live)…"):
            crit = interview_llm.critique_answer(question_text, edited)
        st.markdown(
            f'<div class="ib-card" style="padding:0.8rem 1rem;">'
            f'<b>OBSERVATION</b> {html.escape(crit["observation"])}<br><br>'
            f'<b>WHY IT MATTERS</b> {html.escape(crit["why_it_matters"])}<br><br>'
            f'<b>HOW TO IMPROVE</b> {html.escape(crit["how_to_improve"])}</div>',
            unsafe_allow_html=True)
        if not crit["quote_verified"]:
            st.caption("Couldn't verify the quoted span against your answer verbatim — read this one with a bit more scrutiny.")
    if c4.button("Evaluate", key=f"ib_eval_{question_source}_{row['id']}"):
        result = interview_answers.evaluate_prepared_answer(conn, row["id"])
        if result["status"] == "unavailable":
            st.info(result["detail"])
        else:
            st.write(result)
    n_versions = conn.execute(
        """SELECT COUNT(*) AS n FROM prepared_answer_version
           WHERE process_id=? AND question_source=? AND question_ref_id=?""",
        (process_id, question_source, question_ref_id)).fetchone()["n"]
    c5.caption(f"{n_versions} version(s)")


# ---------------------------------------------------------- read-through queue

def _read_through_section(conn, process_id, claim_row, questions, question_source, section_label):
    rows = []
    for q in questions:
        v = _current_version_row(conn, process_id, question_source, q["id"])
        rows.append((q, v))
    if not rows:
        return

    order = {"insufficient_context": 0, "complete_with_gaps": 1, "complete": 2, None: -1}
    rows.sort(key=lambda r: order.get(r[1]["draft_status"] if r[1] else None, -1))

    st.markdown(f'<div class="ib-label">{section_label}</div>', unsafe_allow_html=True)
    for q, v in rows:
        label = q["question_text"]
        if v:
            gaps = v["body_text"].count("[YOU FILL:")
            gap_note = f" · {gaps} blank(s)" if gaps else ""
            chips = _draft_status_chip(v["draft_status"]) + _review_depth_chip(v["review_depth"])
            with st.expander(f"{label}{gap_note}"):
                st.markdown(chips, unsafe_allow_html=True)
                _answer_editor(conn, process_id, question_source, q["id"],
                               q["question_text"], claim_row["claim_text"])
        else:
            st.caption(f"○ {label} — not generated yet")


def _read_through_queue(conn, process_id, claim_row):
    _read_through_section(conn, process_id, claim_row,
                          _claim_questions(conn, claim_row["id"]),
                          "claim_question", "Follow-up questions")
    metric_qs = _metric_defense_questions(conn, claim_row["id"])
    if metric_qs:
        _read_through_section(conn, process_id, claim_row, metric_qs,
                              "metric_defense", "Metrics defense (this claim's own number)")


# ----------------------------------------------------------- fact review queue

def _fact_review_queue(conn, process_id):
    pending = conn.execute(
        "SELECT * FROM fact_candidate WHERE process_id=? AND status IN ('pending','conflicted') "
        "ORDER BY (status='conflicted') DESC, id DESC LIMIT 20",
        (process_id,)).fetchall()
    if not pending:
        st.caption("No facts waiting for review.")
        return
    st.caption("Every number your edited answers introduce gets checked against your resume. "
              "Confirm it, correct it, or flag a conflict below.")
    for fc in pending:
        conflicted = fc["status"] == "conflicted"
        border = "var(--flag)" if conflicted else "var(--brass)"
        st.markdown(
            f'<div class="ib-card" style="border-left-color:{border}">'
            f'<span class="ib-chip {"ib-chip-gap" if conflicted else "ib-chip-neutral"}">'
            f'{fc["fact_type"]}</span> "{html.escape(fc["source_span"])}"'
            f'<div style="margin-top:0.3em;font-family:monospace;font-size:0.85em">'
            f'value: {html.escape(fc["value"])}{html.escape(fc["unit"] or "")}</div></div>',
            unsafe_allow_html=True)
        if conflicted:
            resume_numbers = interview_answers._resume_claim_numbers(conn, fc["claim_ref"]) \
                if fc["claim_ref"] else set()
            st.caption(f"Conflicts with your resume ({', '.join(resume_numbers) or 'existing value'}).")
            rc1, rc2, rc3 = st.columns(3)
            if rc1.button("Resume is right", key=f"ib_res_{fc['id']}"):
                interview_answers.confirm_fact_candidate(conn, fc["id"], "resume_right")
                conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                               # manager only commits on normal exit, so this must
                               # happen explicitly before every rerun, or the write
                               # this button just made is silently lost.
                st.rerun()
            if rc2.button("New value is right", key=f"ib_new_{fc['id']}"):
                interview_answers.confirm_fact_candidate(conn, fc["id"], "new_value_right")
                conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                               # manager only commits on normal exit, so this must
                               # happen explicitly before every rerun, or the write
                               # this button just made is silently lost.
                st.rerun()
            if rc3.button("Both — different metrics", key=f"ib_both_{fc['id']}"):
                interview_answers.confirm_fact_candidate(conn, fc["id"], "both_different")
                conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                               # manager only commits on normal exit, so this must
                               # happen explicitly before every rerun, or the write
                               # this button just made is silently lost.
                st.rerun()
        else:
            rc1, rc2 = st.columns(2)
            if rc1.button("Confirm fact", key=f"ib_confirm_{fc['id']}"):
                interview_answers.confirm_fact_candidate(conn, fc["id"])
                conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                               # manager only commits on normal exit, so this must
                               # happen explicitly before every rerun, or the write
                               # this button just made is silently lost.
                st.rerun()
            if rc2.button("Reject", key=f"ib_reject_{fc['id']}"):
                interview_answers.reject_fact_candidate(conn, fc["id"])
                conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                               # manager only commits on normal exit, so this must
                               # happen explicitly before every rerun, or the write
                               # this button just made is silently lost.
                st.rerun()


# ------------------------------------------------------------ base questions

def _base_question_by_id(qid):
    return next((q for q in qb.BASE_QUESTIONS if q["id"] == qid), None)


def _base_question_row(conn, process_id, q, master_resume, starred=False):
    v = _current_version_row(conn, process_id, "base_question", q["id"])
    star = " ⭐" if starred else ""
    label = q["text"]
    if v:
        gaps = v["body_text"].count("[YOU FILL:")
        gap_note = f" · {gaps} blank(s)" if gaps else ""
        chips = _draft_status_chip(v["draft_status"]) + _review_depth_chip(v["review_depth"])
        with st.expander(f"{label}{gap_note}{star}"):
            st.markdown(chips, unsafe_allow_html=True)
            _answer_editor(conn, process_id, "base_question", q["id"], q["text"],
                           qb.CATEGORY_LABELS.get(q["category"], ""))
    else:
        cols = st.columns([5, 1])
        cols[0].caption(f"○ {label}{star}")
        if cols[1].button("Generate", key=f"ib_gen_base_{q['id']}"):
            with st.spinner("Generating…"):
                interview_answers.generate_answer_for_question(
                    conn, process_id, "base_question", q["id"], q["text"],
                    None, master_resume)
            conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                           # manager only commits on normal exit, so this must
                           # happen explicitly before every rerun, or the write
                           # this button just made is silently lost.
            st.rerun()


# --------------------------------------------------------------- story bank

def _competency_names(conn):
    return [r["name"] for r in conn.execute("SELECT name FROM competency ORDER BY name").fetchall()]


def _stories_with_competencies(conn):
    rows = conn.execute("SELECT id, title, situation, action, result FROM story ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        comps = [c["name"] for c in conn.execute(
            """SELECT c.name FROM competency c JOIN story_competency sc ON sc.competency_id = c.id
               WHERE sc.story_id = ?""", (r["id"],)).fetchall()]
        out.append((r, comps))
    return out


def _story_bank(conn, master_resume, claims):
    st.caption("Every behavioral/competency question ultimately wants a real story. Map what you "
              "have, and draft new ones from a resume claim when a competency has nothing yet.")

    gaps = interview_stories.coverage_gaps(conn)
    if gaps:
        st.caption("No story mapped yet for: " + ", ".join(g.replace("_", " ") for g in gaps))

    stories = _stories_with_competencies(conn)
    if stories:
        for row, comps in stories:
            comp_chips = "".join(f'<span class="ib-chip ib-chip-neutral">{c}</span>' for c in comps) \
                or '<span class="ib-chip ib-chip-gap">unmapped</span>'
            st.markdown(
                f'<div class="ib-card" style="padding:0.6rem 1rem;"><b>{html.escape(row["title"])}</b><br>'
                f'{_render_fillin_blanks(row["result"] or "")}<div style="margin-top:0.4em">{comp_chips}</div></div>',
                unsafe_allow_html=True)
            if not comps:
                comp_choice = st.selectbox("Map to competency", options=_competency_names(conn),
                                           key=f"ib_story_comp_{row['id']}")
                if st.button("Map", key=f"ib_map_story_{row['id']}"):
                    interview_stories.map_story_to_competency(conn, row["id"], comp_choice)
                    conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                                   # manager only commits on normal exit, so this must
                                   # happen explicitly before every rerun, or the write
                                   # this button just made is silently lost.
                    st.rerun()
    else:
        st.caption("No stories yet — draft one from a claim below.")

    st.markdown('<div class="ib-label">Draft a new story from a resume claim</div>',
               unsafe_allow_html=True)
    if not claims:
        st.caption("No resume claims available yet.")
        return
    default_idx = min(st.session_state.get("ib_claim_select", 0), len(claims) - 1)
    claim_idx = st.selectbox("Base it on this claim", options=range(len(claims)),
                             format_func=lambda i: claims[i]["claim_text"][:90],
                             key="ib_story_claim_select", index=default_idx)
    claim_row = claims[claim_idx]
    if st.button("Draft a story from this claim (SITAR, live)", key="ib_draft_story"):
        with st.spinner("Drafting a first-pass story…"):
            claim = dict(id=claim_row["id"], claim_text=claim_row["claim_text"],
                        source_company=claim_row["source_company"],
                        source_role=claim_row["source_role"])
            try:
                result = interview_stories.draft_story_from_claim(
                    conn, claim, master_resume, claim_row["claim_text"][:60])
            except Exception as e:
                st.error(f"Draft failed: {e}")
            else:
                if result["ok"]:
                    st.success("Story drafted — see it in the list above, and map it to a competency.")
                    conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                                   # manager only commits on normal exit, so this must
                                   # happen explicitly before every rerun, or the write
                                   # this button just made is silently lost.
                    st.rerun()
                else:
                    st.error(f"Rejected: {result['violations']}")


# ------------------------------------------------------------- question bank

def _all_questions_for_bank(conn, process_id):
    """Flattens claim_question + metric_defense (global, shared across every
    process per T§14) and the FULL base question bank (not just the
    per-process top-10 ranked slice) into one list -- a consolidation of
    data that already exists, not a new question source. Each row carries a
    0-1 priority so risk-derived claim questions and importance-derived base
    questions sort on the same axis."""
    recommended_ids = {r["source_ref_id"] for r in conn.execute(
        "SELECT source_ref_id FROM prep_topic WHERE process_id=? AND source='base_question_bank'",
        (process_id,)).fetchall()}
    rows = []
    for c in conn.execute("SELECT id, claim_text, category, risk_level FROM resume_claim").fetchall():
        for q in conn.execute(
                "SELECT id, question_text FROM claim_question WHERE claim_id=?", (c["id"],)).fetchall():
            v = _current_version_row(conn, process_id, "claim_question", q["id"])
            rows.append({
                "Question": q["question_text"], "Category": f"Claim — {c['category']}",
                "Priority": round(c["risk_level"] / 5, 2),
                "Status": v["draft_status"] if v else "not generated",
                "Reviewed": v["review_depth"] if v else "—",
            })
        for q in conn.execute(
                "SELECT id, question_text FROM metric_defense WHERE claim_id=?", (c["id"],)).fetchall():
            v = _current_version_row(conn, process_id, "metric_defense", q["id"])
            rows.append({
                "Question": q["question_text"], "Category": "Metrics defense",
                "Priority": round(c["risk_level"] / 5, 2),
                "Status": v["draft_status"] if v else "not generated",
                "Reviewed": v["review_depth"] if v else "—",
            })
    for q in qb.BASE_QUESTIONS:
        v = _current_version_row(conn, process_id, "base_question", q["id"])
        rows.append({
            "Question": q["text"] + (" ⭐" if q["id"] in recommended_ids else ""),
            "Category": qb.CATEGORY_LABELS.get(q["category"], q["category"]),
            "Priority": 1.0 if q["id"] in recommended_ids else round(
                qb.CATEGORY_BASE_IMPORTANCE.get(q["category"], 0.5) * 0.9, 2),
            "Status": v["draft_status"] if v else "not generated",
            "Reviewed": v["review_depth"] if v else "—",
        })
    return rows


def _question_bank_table(conn, process_id):
    rows = _all_questions_for_bank(conn, process_id)
    if not rows:
        return
    import pandas as pd
    st.caption(f"{len(rows)} questions across claims, metrics defense, and the full base bank. "
              "⭐ marks questions ranked highest-priority for this JD. Read-only — use the tabs "
              "below (or Resume Claims) to actually generate or edit an answer.")
    categories = sorted({r["Category"] for r in rows})
    chosen = st.multiselect("Filter by category", options=categories, default=[],
                            key="ib_qbank_filter")
    search = st.text_input("Search question text", key="ib_qbank_search")
    filtered = [r for r in rows if not chosen or r["Category"] in chosen]
    if search:
        filtered = [r for r in filtered if search.lower() in r["Question"].lower()]
    df = pd.DataFrame(sorted(filtered, key=lambda r: -r["Priority"]))
    st.dataframe(df, hide_index=True, width="stretch")


def _question_bank_tab(conn, process_id, master_resume):
    st.caption("Every question you might get asked, organized into buckets by tab. Every base "
              "question is included in its bucket — ⭐ marks the ones ranked highest-priority "
              "for this specific JD, but nothing is hidden or capped.")
    with st.expander("🔎 Search across every question (flat table)"):
        _question_bank_table(conn, process_id)

    recommended_ids = {r["source_ref_id"] for r in conn.execute(
        "SELECT source_ref_id FROM prep_topic WHERE process_id=? AND source='base_question_bank'",
        (process_id,)).fetchall()}

    cat_keys = list(qb.CATEGORY_LABELS.keys())
    cat_tabs = st.tabs([qb.CATEGORY_LABELS[c] for c in cat_keys])
    for cat_key, tab in zip(cat_keys, cat_tabs):
        with tab:
            qs = [q for q in qb.BASE_QUESTIONS if q["category"] == cat_key]
            st.caption(f"{len(qs)} questions in this bucket.")
            for q in qs:
                _base_question_row(conn, process_id, q, master_resume,
                                   starred=q["id"] in recommended_ids)


# --------------------------------------------------------------- claims tab

def _claims_tab(conn, process_id, claims, master_resume):
    st.caption("Every resume bullet becomes a claim. Pick one below: you'll get Follow-up "
              "Questions (how an interviewer probes it) and, if it carries a number, a Metrics "
              "Defense set (interrogating that number specifically).")
    if not claims:
        st.info("No resume claims found — this shouldn't happen once the "
                "candidate model has been built.")
        return
    claim_labels = [f"{c['claim_text'][:90]}" for c in claims]
    claim_idx = st.selectbox("Choose a claim to prepare", options=range(len(claims)),
                             format_func=lambda i: claim_labels[i], key="ib_claim_select")
    claim_row = claims[claim_idx]
    st.caption(f"Risk level {claim_row['risk_level']}/5 — higher means a metric with "
              "unclear ownership, exactly the combination a follow-up exposes.")

    has_any = any(_current_version_row(conn, process_id, "claim_question", q["id"])
                 for q in _claim_questions(conn, claim_row["id"])) or \
              any(_current_version_row(conn, process_id, "metric_defense", q["id"])
                 for q in _metric_defense_questions(conn, claim_row["id"]))
    if not has_any:
        st.info("Generate first-pass answers using your confirmed facts and stories. "
                "Anything only you know will be left blank for you to fill.")
        if st.button("Generate answers for this claim", type="primary", key="ib_generate_claim"):
            _generate_for_claim(conn, process_id, claim_row, master_resume)
            conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                           # manager only commits on normal exit, so this must
                           # happen explicitly before every rerun, or the write
                           # this button just made is silently lost.
            st.rerun()
    else:
        if st.button("Regenerate any missing", key="ib_generate_claim_more"):
            _generate_for_claim(conn, process_id, claim_row, master_resume)
            conn.commit()  # st.rerun() unwinds via exception -- connect()'s context
                           # manager only commits on normal exit, so this must
                           # happen explicitly before every rerun, or the write
                           # this button just made is silently lost.
            st.rerun()
        _read_through_queue(conn, process_id, claim_row)


# ------------------------------------------------------------- overview tab

def _overview_tab(conn, process_id):
    fit = _fit_rollup(conn, process_id)
    if fit is not None:
        st.markdown(f'<div class="ib-label">Your fit against this JD</div>'
                   f'<div style="font-size:1.4rem;color:var(--brass)">{fit}%</div>'
                   f'<div style="opacity:0.7;font-size:0.85em">Weighted by requirement tier '
                   f'(must-have counts more) — a rollup of the matched/partial/gap calls, '
                   f'not a new judgment.</div>', unsafe_allow_html=True)
    st.markdown("")
    _progress_bar(conn, process_id)
    st.markdown("")
    _focus_list(conn, process_id)


# ---------------------------------------------------------------------- main

def render():
    _inject_css()
    st.caption("Practice, Readiness, and Interview Day come online once Phase 2 "
              "(evaluation engine, mastery, readiness) is built — this is Phase 1 "
              "(Preparation) only, for real.")
    _ensure_db()
    master_resume = _master_resume()
    _ensure_candidate_model(master_resume)

    with interview_store.connect() as conn:
        _process_switcher(conn)
        process_id = st.session_state.get("ib_active_process_id")
        if not process_id:
            return

        claims = _claim_options(conn)
        fact_count = conn.execute(
            "SELECT COUNT(*) AS n FROM fact_candidate WHERE process_id=? AND status IN ('pending','conflicted')",
            (process_id,)).fetchone()["n"]
        fact_tab_label = f"✅ Fact Review ({fact_count})" if fact_count else "✅ Fact Review"

        tab_overview, tab_claims, tab_bank, tab_stories, tab_facts = st.tabs([
            "🎯 Overview", "📋 Resume Claims", "🗂️ Question Bank", "📖 Story Bank", fact_tab_label,
        ])

        with tab_overview:
            _overview_tab(conn, process_id)
        with tab_claims:
            _claims_tab(conn, process_id, claims, master_resume)
        with tab_bank:
            _question_bank_tab(conn, process_id, master_resume)
        with tab_stories:
            _story_bank(conn, master_resume, claims)
        with tab_facts:
            _fact_review_queue(conn, process_id)
