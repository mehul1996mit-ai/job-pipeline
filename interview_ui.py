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

def _progress_bar(conn, process_id, plan):
    """Coverage against the questions that actually matter for THIS process,
    not against every question that exists.

    The first version of this measured against all 313 (every claim question
    x every claim, plus all 83 base questions) and read "2%". Nobody drafts
    313 answers -- an interviewer asks ~10 a round -- so that denominator was
    unreachable by construction, which made a real effort look like no
    progress. Measuring against the ranked plan is both honest and
    achievable. Still a plain count, never a readiness score (E3/I7)."""
    if not plan:
        return
    total = len(plan)
    drafted = sum(1 for i in plan if i["prepared"] > 0)
    worked = sum(1 for i in plan if i["prepared"] >= 1.0)
    pct = round(100 * drafted / total)
    st.markdown(
        f'<div class="ib-progress-track"><div class="ib-progress-fill" style="width:{pct}%"></div></div>'
        f'<div style="font-size:0.82em;opacity:0.75">{drafted} of {total} priority questions '
        f'have an answer · {worked} you\'ve actually worked on. Counts your shortlist, not '
        f'all {len(qb.BASE_QUESTIONS)}+ questions in the bank — no one prepares those.</div>',
        unsafe_allow_html=True)


# ----------------------------------------------------------------- prep plan

_PREP_STATE_CHIP = {
    0.0: ('<span class="ib-chip ib-chip-gap">no answer yet</span>'),
    0.5: ('<span class="ib-chip ib-chip-neutral">draft — unreviewed</span>'),
    1.0: ('<span class="ib-chip ib-chip-complete">you\'ve worked on it</span>'),
}


def _prep_plan(conn, process_id, days_to_interview, master_resume):
    """The screen you actually prepare from: a short ranked list of real
    questions, highest-value first. Replaces the old grouped focus list,
    which sorted by the SYSTEM's internal source type (requirement_gap /
    high_risk_claim / ...) rather than by what you're most likely to be
    asked, and phrased every row as a topic rather than as a question
    anybody would actually say out loud."""
    plan = interview_prep.build_prep_plan(conn, process_id, days_to_interview)
    if not plan:
        st.caption("Nothing to plan yet — create a process from a JD first.")
        return

    if days_to_interview is not None and days_to_interview <= 1:
        horizon = "Interview is imminent — this is the short drill list."
    elif days_to_interview is not None and days_to_interview <= 7:
        horizon = f"{days_to_interview} days out — enough time to write these properly."
    else:
        horizon = "Plenty of runway — work down this list, and build stories in parallel."
    st.markdown(f'<div class="ib-label">Prepare these first · {len(plan)} questions</div>'
               f'<div style="font-size:0.85em;opacity:0.75;margin-bottom:0.6rem">{horizon} '
               f'Ranked by how likely you are to be asked and how much it costs you to '
               f'fumble it. Answered items sink but stay visible.</div>',
               unsafe_allow_html=True)

    for i, item in enumerate(plan, 1):
        chip = _PREP_STATE_CHIP.get(item["prepared"], "")
        context = item.get("context") or ""
        ctx_html = (f'<div style="opacity:0.6;font-size:0.8em;margin-top:0.35em">'
                    f'{html.escape(_trunc(context, 120))}</div>'
                    if context else "")
        with st.expander(f"{i}. {item['question_text']}"):
            st.markdown(
                f'<div style="margin-bottom:0.4rem">{chip}</div>'
                f'<div style="font-size:0.86em;opacity:0.8">{html.escape(item["why"])}</div>'
                f'{ctx_html}', unsafe_allow_html=True)
            if item["prepared"] > 0:
                _answer_editor(conn, process_id, item["question_source"],
                               item["question_ref_id"], item["question_text"], context,
                               scope="plan")
            else:
                if st.button("Draft an answer", type="primary",
                             key=f"ib_plan_gen_{item['question_source']}_{item['question_ref_id']}"):
                    claim = None
                    if item["question_source"] != "base_question" and context:
                        claim = {"claim_text": context, "source_company": None}
                    with st.spinner("Drafting…"):
                        interview_answers.generate_answer_for_question(
                            conn, process_id, item["question_source"],
                            item["question_ref_id"], item["question_text"],
                            claim, master_resume)
                    conn.commit()  # st.rerun() unwinds via exception -- see the
                                   # note at the other rerun sites.
                    st.rerun()


def _open_gaps_view(conn, process_id):
    """Kept separate from the question plan on purpose: a JD gap is not a
    question, it's a position you need to hold when they push on it."""
    gaps = interview_prep.open_gaps(conn, process_id)
    if not gaps:
        return
    st.markdown('<div class="ib-label">Where this JD outruns your resume</div>'
               '<div style="font-size:0.85em;opacity:0.75;margin-bottom:0.4rem">'
               'They will find these. Have an honest line ready for each — what you '
               'have done that is adjacent, and how fast you would close it.</div>',
               unsafe_allow_html=True)
    for g in gaps:
        tone = "ib-chip-gap" if g["match_status"] == "gap" else "ib-chip-neutral"
        st.markdown(
            f'<div class="ib-card" style="padding:0.5rem 0.9rem;">'
            f'<span class="ib-chip {tone}">{g["tier"].replace("_", "-")}</span>'
            f'{html.escape(g["requirement_text"])}</div>', unsafe_allow_html=True)


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


def _answer_editor(conn, process_id, question_source, question_ref_id, question_text,
                   claim_text, scope="x"):
    """`scope` namespaces every widget key to the CALLER. Streamlit executes
    all tab bodies on every run, and the same question can now legitimately
    appear in more than one tab (the prep plan surfaces questions that also
    live under Resume Claims), so an unscoped key collides outright --
    StreamlitDuplicateElementKey, found live the moment the plan shipped."""
    row = _current_version_row(conn, process_id, question_source, question_ref_id)
    if not row:
        st.caption("No draft yet.")
        return

    interview_answers.mark_skimmed(conn, row["id"])
    uid = f"{scope}_{question_source}_{row['id']}"

    # Drill mode: hide the answer, try to say it, then reveal. Re-reading a
    # prepared answer feels like preparation and mostly isn't -- retrieval
    # practice is the part that survives into the room. Needs no Phase 2
    # engine: it's a visibility toggle over content that already exists.
    drill_key = f"ib_drill_{uid}"
    drilling = st.toggle("🎤 Drill mode — hide the answer, say it out loud first",
                         key=drill_key)
    if drilling:
        st.markdown(
            '<div class="ib-card" style="text-align:center;opacity:0.75">'
            'Answer hidden. Say your version out loud, all the way through, '
            'then reveal and compare.</div>', unsafe_allow_html=True)
        if not st.checkbox("Reveal answer", key=f"{drill_key}_reveal"):
            return

    st.markdown(f'<div class="ib-label">What this tests</div>'
               f'<div style="opacity:0.85;font-size:0.9em;margin-bottom:0.6rem">'
               f'{html.escape(claim_text or "")}</div>', unsafe_allow_html=True)

    chips = _draft_status_chip(row["draft_status"]) + _review_depth_chip(row["review_depth"])
    st.markdown(f'<div style="margin-bottom:0.4rem">{chips}</div>', unsafe_allow_html=True)

    preview_html = _render_fillin_blanks(row["body_text"])
    st.markdown(f'<div class="ib-card">{preview_html}</div>', unsafe_allow_html=True)

    edit_key = f"ib_edit_{uid}"
    edited = st.text_area("Edit this answer", value=row["body_text"], height=180, key=edit_key)

    c1, c2, c3, c4, c5 = st.columns(5)
    if c1.button("Save revision", key=f"ib_save_{uid}"):
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
    if c2.button("Regenerate", key=f"ib_regen_{uid}"):
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
    if c3.button("Critique", key=f"ib_critique_{uid}"):
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
    if c4.button("Evaluate", key=f"ib_eval_{uid}"):
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
                               q["question_text"], claim_row["claim_text"], scope="claim")
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
                           qb.CATEGORY_LABELS.get(q["category"], ""), scope="bank")
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

# The five SITAR fields you'd actually speak; the eight "detail" fields
# (team_size, exact_role, ...) exist to be filled in, not read as prose.
_SITAR_FIELDS = ("situation", "task", "action", "result", "reflection")
_SITAR_LABELS = {"situation": "Situation", "task": "Task", "action": "Action",
                 "result": "Result", "reflection": "Reflection"}


def _trunc(text: str, max_len: int) -> str:
    """Truncate at a word boundary with a real ellipsis -- never a hard
    mid-word cut. A bare text[:N] slice (the previous behavior, e.g.
    "...Personal Loan product, gro") reads as a broken resume line even
    though the underlying data is complete; this only affects display."""
    text = text or ""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return (cut or text[:max_len]) + "…"


def _competency_names(conn):
    return [r["name"] for r in conn.execute("SELECT name FROM competency ORDER BY name").fetchall()]


def _stories_with_competencies(conn):
    rows = conn.execute("SELECT * FROM story ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        comps = [c["name"] for c in conn.execute(
            """SELECT c.name FROM competency c JOIN story_competency sc ON sc.competency_id = c.id
               WHERE sc.story_id = ?""", (r["id"],)).fetchall()]
        out.append((r, comps))
    return out


def _coverage_chips(conn):
    """Every competency at a glance, covered vs. gap -- replaces a plain
    caption sentence ("No story mapped yet for: leadership, ownership,
    conflict...") with the same chip language the rest of the app uses,
    so this reads as status, not as a debug string."""
    names = _competency_names(conn)
    gaps = set(interview_stories.coverage_gaps(conn))
    chips = "".join(
        f'<span class="ib-chip {"ib-chip-gap" if n in gaps else "ib-chip-complete"}">'
        f'{n.replace("_", " ")}</span>' for n in names)
    st.markdown(f'<div style="margin-bottom:0.6rem">{chips}</div>', unsafe_allow_html=True)


def _story_detail_fields(conn, story_row):
    """The eight supporting fields (team_size, exact_role, decision_made,
    stakeholders, metrics, tradeoff, failure, learning) every drafted story
    defaults to a literal "[YOU FILL: ...]" placeholder for -- I4's whole
    point is that you notice and fill these, which was impossible while
    nothing in the UI ever showed them. Nested under its own expander so the
    main story card stays readable; only flagged in the header when
    something is still unfilled."""
    unfilled = [f for f in interview_stories.PLACEHOLDER_FIELDS
               if (story_row[f] or "").startswith("[YOU FILL:")]
    label = f"Fill in the details ({len(unfilled)} left)" if unfilled else "Details — all filled in"
    with st.expander(label):
        edits = {}
        cols = st.columns(2)
        for i, f in enumerate(interview_stories.PLACEHOLDER_FIELDS):
            edits[f] = cols[i % 2].text_input(
                f.replace("_", " ").title(), value=story_row[f] or "",
                key=f"ib_story_detail_{f}_{story_row['id']}")
        if st.button("Save details", key=f"ib_story_detail_save_{story_row['id']}"):
            interview_stories.update_story(conn, story_row["id"], None, **edits)
            conn.commit()  # st.rerun() unwinds via exception -- see the other rerun sites.
            st.rerun()


def _story_card(conn, master_resume, story_row, comps):
    comp_chips = "".join(f'<span class="ib-chip ib-chip-neutral">{c}</span>' for c in comps) \
        or '<span class="ib-chip ib-chip-gap">not mapped to a competency</span>'
    preview = _trunc(story_row["situation"] or story_row["result"] or "", 90)
    with st.expander(f"{story_row['title']} — {preview}"):
        st.markdown(f'<div style="margin-bottom:0.5rem">{comp_chips}</div>', unsafe_allow_html=True)

        edits = {}
        for f in _SITAR_FIELDS:
            preview_html = _render_fillin_blanks(story_row[f] or "")
            st.markdown(f'<div class="ib-label">{_SITAR_LABELS[f]}</div>'
                       f'<div class="ib-card" style="padding:0.5rem 0.9rem;margin-bottom:0.4rem">'
                       f'{preview_html or "<i>empty</i>"}</div>', unsafe_allow_html=True)
            edits[f] = st.text_area(_SITAR_LABELS[f], value=story_row[f] or "", height=70,
                                    label_visibility="collapsed",
                                    key=f"ib_story_edit_{f}_{story_row['id']}")

        c1, c2, c3 = st.columns([1, 1, 1])
        if c1.button("Save", key=f"ib_story_save_{story_row['id']}"):
            interview_stories.update_story(conn, story_row["id"], master_resume, **edits)
            conn.commit()  # st.rerun() unwinds via exception -- see the other rerun sites.
            st.rerun()
        if not comps:
            comp_choice = c2.selectbox("Map to", options=_competency_names(conn),
                                       key=f"ib_story_comp_{story_row['id']}",
                                       label_visibility="collapsed")
            if c3.button("Map", key=f"ib_map_story_{story_row['id']}"):
                interview_stories.map_story_to_competency(conn, story_row["id"], comp_choice)
                conn.commit()  # st.rerun() unwinds via exception -- see the other rerun sites.
                st.rerun()
        if st.button("Delete this story", key=f"ib_story_delete_{story_row['id']}"):
            interview_stories.delete_story(conn, story_row["id"])
            conn.commit()  # st.rerun() unwinds via exception -- see the other rerun sites.
            st.rerun()

        _story_detail_fields(conn, story_row)


def _story_bank(conn, master_resume, claims):
    st.caption("Every behavioral question wants a real story behind it. Map what you have, fill "
              "in the blanks a first draft can't know, and draft a new one where a competency has "
              "nothing yet.")

    _coverage_chips(conn)

    stories = _stories_with_competencies(conn)
    if stories:
        for row, comps in stories:
            _story_card(conn, master_resume, row, comps)
    else:
        st.caption("No stories yet — draft one from a claim below.")

    st.markdown('<div class="ib-label">Draft a new story from a resume claim</div>',
               unsafe_allow_html=True)
    if not claims:
        st.caption("No resume claims available yet.")
        return
    default_idx = min(st.session_state.get("ib_claim_select", 0), len(claims) - 1)
    claim_idx = st.selectbox("Base it on this claim", options=range(len(claims)),
                             format_func=lambda i: _trunc(claims[i]["claim_text"], 90),
                             key="ib_story_claim_select", index=default_idx)
    claim_row = claims[claim_idx]
    if st.button("Draft a story from this claim (SITAR, live)", key="ib_draft_story"):
        with st.spinner("Drafting a first-pass story…"):
            claim = dict(id=claim_row["id"], claim_text=claim_row["claim_text"],
                        source_company=claim_row["source_company"],
                        source_role=claim_row["source_role"])
            try:
                result = interview_stories.draft_story_from_claim(
                    conn, claim, master_resume, _trunc(claim_row["claim_text"], 60))
            except Exception as e:
                st.error(f"Draft failed: {e}")
            else:
                if result["ok"]:
                    st.success("Story drafted — see it in the list above. Open it to read the "
                              "full story, fill in any blanks, and map it to a competency.")
                    conn.commit()  # st.rerun() unwinds via exception -- see the other rerun sites.
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
    claim_labels = [_trunc(c["claim_text"], 90) for c in claims]
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

def _jd_quality_banner(conn, process_id, master_resume):
    """The deterministic extractor is job_pipeline's bulk n-gram scorer. On a
    single JD read literally, it emits fragments ('gathering', 'seamless',
    one literally named 'key') as must-have requirements -- and the fit
    rollup is computed over that same set, so a polluted read is a
    misleading percentage, not just an ugly list. Offer the one-call upgrade
    explicitly rather than silently spending it at intake."""
    row = conn.execute(
        "SELECT analyst, COUNT(*) AS n FROM jd_requirement WHERE process_id=? GROUP BY analyst",
        (process_id,)).fetchone()
    if row and row["analyst"] == "llm":
        return
    st.warning(
        "**JD requirements were read by the fast lexical extractor** — it's built for "
        "scoring 500 postings a day, not for reading one closely, so it emits filler "
        "fragments as if they were requirements. Your fit % is computed over that same "
        "list. One model call re-reads this JD properly.")
    if st.button("Re-read this JD properly", type="primary", key="ib_reanalyze_jd"):
        with st.spinner("Re-reading the JD…"):
            result = interview_prep.reanalyze_process_jd(conn, process_id, master_resume)
        if result["ok"]:
            st.success(f"Re-read done — {len(result['requirement_ids'])} real requirements.")
            conn.commit()  # st.rerun() unwinds via exception -- see the other rerun sites.
            st.rerun()
        else:
            st.error("Couldn't get a clean model read just now — keeping the existing "
                     "requirements rather than dropping them. Try again in a moment.")


def _overview_tab(conn, process_id, days_to_interview, master_resume):
    _jd_quality_banner(conn, process_id, master_resume)

    plan = interview_prep.build_prep_plan(conn, process_id, days_to_interview)
    _prep_plan(conn, process_id, days_to_interview, master_resume)

    st.divider()
    _open_gaps_view(conn, process_id)

    st.divider()
    # Deliberately BELOW the plan and the gaps. These are scoreboard numbers:
    # useful to glance at, never the thing you act on, and putting them at the
    # top (as the first build did) pushed the actual work below the fold.
    with st.expander("📊 Where you stand (fit %, coverage)"):
        fit = _fit_rollup(conn, process_id)
        if fit is not None:
            st.markdown(f'<div class="ib-label">Fit against this JD</div>'
                       f'<div style="font-size:1.3rem;color:var(--brass)">{fit}%</div>'
                       f'<div style="opacity:0.7;font-size:0.82em">Weighted by requirement tier. '
                       f'A rollup of the matched/partial/gap calls — only as good as the '
                       f'requirement list it runs over.</div>', unsafe_allow_html=True)
        st.markdown("")
        _progress_bar(conn, process_id, plan)


# ------------------------------------------------------- night-before export

def _night_before_export(conn, process_id, days_to_interview):
    """One flat page of your prepared answers, downloadable.

    The hour before an interview you are on a phone in a lobby, not clicking
    through five tabs and ten sub-tabs. Everything here already exists in the
    app -- this is purely a different surface for it, which is exactly why
    it's worth having."""
    with st.expander("📄 Export a one-page prep sheet (for the night before)"):
        proc = conn.execute(
            "SELECT company_name, role_title FROM interview_process WHERE id=?",
            (process_id,)).fetchone()
        plan = interview_prep.build_prep_plan(conn, process_id, days_to_interview, limit=40)
        gaps = interview_prep.open_gaps(conn, process_id, limit=8)

        lines = [f"# {proc['company_name']} — {proc['role_title']}", ""]
        if days_to_interview is not None:
            lines += [f"_Interview in {days_to_interview} day(s)._", ""]
        lines += ["## Questions to have ready", ""]
        for i, item in enumerate(plan, 1):
            v = _current_version_row(conn, process_id, item["question_source"],
                                     item["question_ref_id"])
            lines.append(f"**{i}. {item['question_text']}**")
            lines.append("")
            lines.append(v["body_text"] if v else "_(no answer prepared yet)_")
            lines.append("")
        if gaps:
            lines += ["## Gaps they may push on", ""]
            lines += [f"- **{g['requirement_text']}** ({g['tier'].replace('_','-')}, "
                      f"{g['match_status']})" for g in gaps]
            lines.append("")
        md = "\n".join(lines)

        answered = sum(1 for i in plan if i["prepared"] > 0)
        st.caption(f"{len(plan)} questions, {answered} with a prepared answer.")
        st.download_button(
            "⬇️ Download prep sheet (.md)", data=md.encode("utf-8"),
            file_name=f"prep_{(proc['company_name'] or 'interview').replace(' ', '_')}.md",
            mime="text/markdown", key="ib_export_md")


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

        sched = conn.execute(
            "SELECT scheduled_date FROM interview_process WHERE id=?",
            (process_id,)).fetchone()
        days_to_interview = _days_to(sched["scheduled_date"]) if sched else None

        # Tab order is the actual prep workflow, not a tour of the data model:
        # what to do now -> the claims they'll dig into -> the full bank for
        # lookup -> stories -> housekeeping. Fact Review is last because it's
        # a chore, not preparation.
        tab_plan, tab_claims, tab_bank, tab_stories, tab_facts = st.tabs([
            "🎯 Prep Plan", "📋 Resume Claims", "🗂️ Question Bank", "📖 Story Bank", fact_tab_label,
        ])

        with tab_plan:
            _overview_tab(conn, process_id, days_to_interview, master_resume)
        with tab_claims:
            _claims_tab(conn, process_id, claims, master_resume)
        with tab_bank:
            _question_bank_tab(conn, process_id, master_resume)
        with tab_stories:
            _story_bank(conn, master_resume, claims)
        with tab_facts:
            _fact_review_queue(conn, process_id)

        st.divider()
        _night_before_export(conn, process_id, days_to_interview)
