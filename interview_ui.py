"""Streamlit UI for the Interview Prep toolkit — extends streamlit_app.py
(one new tab calls render() from here) rather than forking the app, per the
UI master prompt's implementation-reality constraint.

Scope: only screens backed by real, built subsystems. Practice, Readiness,
and Interview Day all depend on Phase 2 (evaluation engine T§5, mastery T§8,
readiness T§9, mock interview T§10) — none of that exists yet, so those
screens are NOT built here; a plain note explains why rather than shipping
a screen with no real data behind it. Company/segment research (T§11) is
the same story. What IS built: the process switcher (T§14) and the full
🎯 Prepare screen (E§4) — read-through queue, answer editor with the
fill-in-blank device, fact review queue (E§6).

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

    cols = st.columns([1] * max(len(processes), 1) + [1])
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


# --------------------------------------------------------------- prep topics

def _render_prep_topics(conn, process_id):
    topics = conn.execute(
        """SELECT topic_text, source, priority, rationale FROM prep_topic
           WHERE process_id = ? ORDER BY priority DESC LIMIT 12""",
        (process_id,)).fetchall()
    if not topics:
        st.caption("No prep topics yet for this process.")
        return
    st.markdown('<div class="ib-label">What to prioritize</div>', unsafe_allow_html=True)
    for t in topics:
        rationale = f"<div style='opacity:0.75;font-size:0.85em;margin-top:0.2em'>{html.escape(t['rationale'])}</div>" \
            if t["rationale"] else ""
        st.markdown(
            f'<div class="ib-card" style="padding:0.6rem 1rem;">'
            f'<span class="ib-chip ib-chip-neutral">{t["source"].replace("_"," ")}</span>'
            f'{html.escape(t["topic_text"])}{rationale}</div>',
            unsafe_allow_html=True)


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
    """Generates BOTH the question tree (T§4.4) and, if this claim carries a
    metric, the metrics-defense set (T§4.5) — a claim's full preparation
    surface, not just the question tree."""
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
            st.success(f"Saved. {len(fact_ids)} fact(s) detected for review below." if fact_ids else "Saved.")
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
                          "claim_question", "Question tree")
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
    st.markdown('<div class="ib-label">Fact review queue</div>', unsafe_allow_html=True)
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
    import interview_question_bank as qb
    return next((q for q in qb.BASE_QUESTIONS if q["id"] == qid), None)


def _base_questions_section(conn, process_id, master_resume):
    """PM-fundamentals/behavioral/product-sense/target-company/HR questions
    — the categories the claim-derived tree can't produce. Pulls the
    already-computed, already-capped selection from prep_topic
    (source='base_question_bank') rather than recomputing here."""
    topic_rows = conn.execute(
        "SELECT source_ref_id, priority FROM prep_topic WHERE process_id=? "
        "AND source='base_question_bank' ORDER BY priority DESC",
        (process_id,)).fetchall()
    if not topic_rows:
        return
    st.markdown('<div class="ib-label">Base questions (PM fundamentals, behavioral, '
               'product sense, target company, HR)</div>', unsafe_allow_html=True)
    for t in topic_rows:
        q = _base_question_by_id(t["source_ref_id"])
        if not q:
            continue
        v = _current_version_row(conn, process_id, "base_question", q["id"])
        label = q["text"]
        if v:
            gaps = v["body_text"].count("[YOU FILL:")
            gap_note = f" · {gaps} blank(s)" if gaps else ""
            chips = _draft_status_chip(v["draft_status"]) + _review_depth_chip(v["review_depth"])
            with st.expander(f"{label}{gap_note}"):
                st.markdown(chips, unsafe_allow_html=True)
                import interview_question_bank as qb
                _answer_editor(conn, process_id, "base_question", q["id"], q["text"],
                               qb.CATEGORY_LABELS.get(q["category"], ""))
        else:
            cols = st.columns([5, 1])
            cols[0].caption(f"○ {label}")
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


def _story_bank(conn, master_resume, claim_row):
    st.markdown('<div class="ib-label">Story bank</div>', unsafe_allow_html=True)

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
        st.caption("No stories mapped to this competency yet — add one from a claim below.")

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
    process per T§14) and this process's base_question_bank selection into
    one list -- a consolidation of data that already exists, not a new
    question source. Each row carries a 0-1 priority so risk-derived claim
    questions and importance-derived base questions sort on the same axis."""
    import interview_question_bank as qb
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
    for t in conn.execute(
            "SELECT source_ref_id, priority FROM prep_topic WHERE process_id=? "
            "AND source='base_question_bank'", (process_id,)).fetchall():
        q = _base_question_by_id(t["source_ref_id"])
        if not q:
            continue
        v = _current_version_row(conn, process_id, "base_question", q["id"])
        rows.append({
            "Question": q["text"], "Category": qb.CATEGORY_LABELS.get(q["category"], q["category"]),
            "Priority": t["priority"],
            "Status": v["draft_status"] if v else "not generated",
            "Reviewed": v["review_depth"] if v else "—",
        })
    return rows


def _question_bank_view(conn, process_id):
    rows = _all_questions_for_bank(conn, process_id)
    if not rows:
        return
    import pandas as pd
    st.markdown('<div class="ib-label">Question bank — everything in one place</div>',
               unsafe_allow_html=True)
    st.caption(f"{len(rows)} questions across claims, metrics defense, and the base bank. "
              "Sorted by priority — click a column header to re-sort.")
    categories = sorted({r["Category"] for r in rows})
    chosen = st.multiselect("Filter by category", options=categories, default=[],
                            key="ib_qbank_filter")
    filtered = [r for r in rows if not chosen or r["Category"] in chosen]
    df = pd.DataFrame(sorted(filtered, key=lambda r: -r["Priority"]))
    st.dataframe(df, hide_index=True, width="stretch")
    st.caption("Read-only overview — use the claim picker or base questions section "
              "above to actually generate or edit an answer.")


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

        st.divider()
        fit = _fit_rollup(conn, process_id)
        if fit is not None:
            st.markdown(f'<div class="ib-label">Your fit against this JD</div>'
                       f'<div style="font-size:1.4rem;color:var(--brass)">{fit}%</div>'
                       f'<div style="opacity:0.7;font-size:0.85em">Weighted by requirement tier '
                       f'(must-have counts more) — a rollup of the matched/partial/gap calls below, '
                       f'not a new judgment.</div>', unsafe_allow_html=True)
        _render_prep_topics(conn, process_id)
        st.divider()
        with st.expander("📚 Question bank (all questions, filterable)"):
            _question_bank_view(conn, process_id)
        st.divider()

        st.markdown('<div class="ib-label">🎯 Prepare — claims, questions, metrics, stories</div>',
                    unsafe_allow_html=True)
        st.caption("Every resume bullet becomes a claim: a question tree (T§4.4) to defend it, "
                  "a metrics-defense set (T§4.5) if it carries a number, and a story you can map "
                  "to it (T§4.7). Sorted by risk by default, but this covers every claim, not just "
                  "the risky ones.")
        claims = _claim_options(conn)
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

        st.divider()
        _base_questions_section(conn, process_id, master_resume)

        st.divider()
        _story_bank(conn, master_resume, claim_row)

        st.divider()
        _fact_review_queue(conn, process_id)
