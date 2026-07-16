"""LLM tailoring — provider pluggable (gemini | groq | anthropic).

HARD RULE (encoded in the prompt and enforced by design): the model may only
reword/reorder facts already present in the base CV. It must never invent
experience. Gaps are surfaced honestly via honest_gap_note.

Output contract (strict JSON):
  tailored_summary          3-4 sentences, facts from the base CV only
  bullets_to_lead_with      3 existing CV bullets, priority order
  keywords_to_add_if_true   keywords to weave in ONLY if genuinely true
  honest_gap_note           one sentence on what the CV does not support
"""
from __future__ import annotations

import copy
import difflib
import json
import os
import re
import time

import requests

PROMPT_TEMPLATE = """You are helping tailor a job application. Use ONLY facts
present in the BASE CV below. Never invent experience, employers, tools,
metrics, or dates. If the job wants something the CV does not support, say so
in honest_gap_note instead of papering over it.

BASE CV TEXT:
{cv_text}

JOB: {title} at {company}
JOB DESCRIPTION (may be truncated):
{jd}

TOP JD KEYWORDS MISSING FROM THE CV: {missing_keywords}

Return ONLY a JSON object (no markdown fences, no commentary) with exactly
these keys:
{{
  "tailored_summary": "3-4 sentences positioning the candidate for THIS job, using only CV facts",
  "bullets_to_lead_with": ["existing CV bullet 1", "bullet 2", "bullet 3"],
  "keywords_to_add_if_true": ["only keywords genuinely supported by the CV"],
  "honest_gap_note": "one sentence: what this JD asks for that the CV does not demonstrate"
}}"""

EMPTY = {"tailored_summary": "", "bullets_to_lead_with": [],
         "keywords_to_add_if_true": [], "honest_gap_note": ""}


def _extract_json(text: str) -> dict:
    """Parse LLM output that may be fenced or wrapped in prose."""
    if not text:
        return dict(EMPTY)
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        brace = re.search(r"\{.*\}", text, re.S)
        if brace:
            try:
                return json.loads(brace.group(0))
            except json.JSONDecodeError:
                pass
    out = dict(EMPTY)
    out["honest_gap_note"] = "LLM output was not parseable; raw output kept."
    out["_raw"] = text[:2000]
    return out


# ------------------------------------------------------------- providers
def _call_gemini(prompt: str, model: str) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent",
        headers={"x-goog-api-key": key},  # header, not URL: keeps the key out of error messages/CI logs
        json={"contents": [{"parts": [{"text": prompt}]}],
              # thinkingBudget: 0 stops "thinking" models from spending the
              # whole output-token budget on hidden reasoning and returning
              # no visible text (observed on gemini-flash-latest).
              "generationConfig": {"temperature": 0.3,
                                   "maxOutputTokens": 1536,
                                   "thinkingConfig": {"thinkingBudget": 0}}},
        timeout=60)
    r.raise_for_status()
    candidates = r.json().get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def _call_groq(prompt: str, model: str) -> str:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model,
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3, "max_tokens": 1024},
        timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_anthropic(prompt: str, model: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 1024,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120)
    r.raise_for_status()
    body = r.json()
    if body.get("stop_reason") == "refusal":
        return ""
    return "".join(b.get("text", "") for b in body.get("content", [])
                   if b.get("type") == "text")


PROVIDERS = {"gemini": ("gemini_model", _call_gemini),
             "groq": ("groq_model", _call_groq),
             "anthropic": ("anthropic_model", _call_anthropic)}


def tailor_job(cv_text: str, job: dict, missing_keywords: list[str],
               config: dict, log=print) -> dict:
    tcfg = config.get("tailor", {})
    provider = tcfg.get("provider", "gemini")
    if provider not in PROVIDERS:
        log(f"tailor: unknown provider '{provider}' — skipping")
        return dict(EMPTY)
    model_key, call = PROVIDERS[provider]
    model = tcfg.get(model_key)
    prompt = PROMPT_TEMPLATE.format(
        cv_text=cv_text[:9000],
        title=job.get("title", ""), company=job.get("company", ""),
        jd=(job.get("description") or "")[:9000],
        missing_keywords=", ".join(missing_keywords) or "none",
    )
    try:
        raw = call(prompt, model)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (429, 503):
            # One polite retry after a short pause -- not a retry-hammer,
            # just enough to ride out a transient free-tier throttle.
            log(f"tailor: {provider} {status} for '{job.get('title')}' — "
                "retrying once after 20s")
            time.sleep(20)
            try:
                raw = call(prompt, model)
            except Exception as e2:
                log(f"tailor: {provider} retry failed for "
                    f"'{job.get('title')}' ({e2})")
                out = dict(EMPTY)
                out["honest_gap_note"] = f"tailoring skipped ({e2})"
                return out
        else:
            log(f"tailor: {provider} call failed for '{job.get('title')}' "
                f"({e})")
            out = dict(EMPTY)
            out["honest_gap_note"] = f"tailoring skipped ({e})"
            return out
    except Exception as e:
        log(f"tailor: {provider} call failed for '{job.get('title')}' ({e})")
        out = dict(EMPTY)
        out["honest_gap_note"] = f"tailoring skipped ({e})"
        return out
    return _extract_json(raw)


# ------------------------------------------------------- resume file build
def _best_bullet_match(candidate: str, resume: dict, threshold: float = 0.35):
    """Find the ORIGINAL resume bullet closest to an LLM-paraphrased
    candidate string. Returns (company_idx, role_idx, bullet_idx) or None.
    Never returns the paraphrase itself -- only a pointer to real CV text,
    so the rendered resume never contains invented wording.

    The tailor prompt instructs the LLM to return bullets verbatim, so in
    practice candidates are near-exact copies of the source (high ratio).
    If a candidate is unusually short/compressed and falls below
    `threshold`, the match is safely skipped -- that bullet's position is
    left unchanged rather than guessed at, so a low-confidence match can
    never reorder the wrong content."""
    best = None
    best_ratio = threshold
    cand_norm = (candidate or "").lower().strip()
    if not cand_norm:
        return None
    for ci, company in enumerate(resume.get("experience", [])):
        for ri, role in enumerate(company.get("roles", [])):
            for bi, bullet in enumerate(role.get("bullets", [])):
                ratio = difflib.SequenceMatcher(
                    None, cand_norm, bullet.lower()).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best = (ci, ri, bi)
    return best


def build_tailored_resume(master_resume: dict, tailored_fields: dict) -> dict:
    """Produce a tailored resume dict for file rendering.

    HARD RULE: this only REORDERS content that already exists in
    master_resume. It never writes LLM-generated prose into the structured
    resume, except for `summary` (which the tailor prompt is itself
    constrained to build only from CV facts). Name and contact are locked.
    """
    resume = copy.deepcopy(master_resume)

    if tailored_fields.get("tailored_summary"):
        resume["summary"] = tailored_fields["tailored_summary"]

    # Move the lead bullets to the front of their own role's bullet list.
    # Process in REVERSE: each insert(0, ...) pushes prior inserts down one
    # slot, so processing last-to-first leaves the FIRST candidate (the
    # LLM's top priority pick) sitting at position 0 once all are placed.
    # (Forward-order insertion silently reverses the intended priority
    # whenever two lead bullets land in the same role.)
    lead_bullets = tailored_fields.get("bullets_to_lead_with", []) or []
    for candidate in reversed(lead_bullets):
        match = _best_bullet_match(candidate, resume)
        if not match:
            continue
        ci, ri, bi = match
        bullets = resume["experience"][ci]["roles"][ri]["bullets"]
        bullets.insert(0, bullets.pop(bi))

    # Reorder skill groups so the ones matching JD keywords surface first.
    keywords = [k.lower() for k in
               (tailored_fields.get("keywords_to_add_if_true") or [])]
    if keywords and resume.get("skills"):
        def hits(group):
            items = group.get("items", "").lower()
            return sum(1 for k in keywords if k in items)
        resume["skills"] = sorted(resume["skills"], key=hits, reverse=True)

    resume["name"] = master_resume["name"]
    resume["contact_line"] = master_resume["contact_line"]
    return resume
