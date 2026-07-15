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

import json
import os
import re

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
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.3,
                                   "maxOutputTokens": 1024}},
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
    except Exception as e:
        log(f"tailor: {provider} call failed for '{job.get('title')}' ({e})")
        out = dict(EMPTY)
        out["honest_gap_note"] = f"tailoring skipped ({e})"
        return out
    return _extract_json(raw)
