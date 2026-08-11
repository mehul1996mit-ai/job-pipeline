"""LLM-assisted generation for Phase 1 — free-tier only.

Deliberately narrow: the rest of Phase 1 (question trees, metrics defense,
JD gap mapping) stays fully deterministic per interview_prep.py's own design
note, because templates already serve those cases well and an LLM call would
only add cost/latency/fact-risk for no real gain. The one place generation
is genuinely load-bearing is drafting a first-pass Situation/Task/Action/
Result/Reflection narrative from a resume claim (§4.7's "guided capture") --
that's what this module does, and nothing else.

Free-tier only: reuses tailor.py's gemini/groq call plumbing directly
(gemini has a live, working key in this repo already). Anthropic is
excluded on purpose -- tailor.py's anthropic_model is a paid Opus model,
not a free tier, so it's never selected here regardless of what
config.yaml's tailor.provider says.

I3 applies to every function here: LLM output is validated against
resume_master.json via interview_stories.check_fact_integrity() before use.
On a violation, ONE regeneration is attempted with a stricter reminder; a
second violation is a hard failure (raises InterviewFactIntegrityError) --
never a silent pass, never a silently-blanked field.

I6: every function takes an injectable `call_fn(prompt) -> str`, defaulting
to the real free-tier provider call. interview_smoke_test.py passes a fake
double, so this module needs zero API keys/network to be tested -- same
pattern career_agent_smoke_test.py uses for its fake Gmail service.
"""
from __future__ import annotations

import json
import re
import time

import requests

import tailor  # reuse _call_gemini/_call_groq/_extract_json — provider plumbing only
from interview_stories import check_fact_integrity, _all_resume_numbers

FREE_PROVIDERS = {"gemini": tailor._call_gemini, "groq": tailor._call_groq}


class InterviewFactIntegrityError(RuntimeError):
    """Raised when generated content still asserts an unsupported fact after
    one regeneration attempt. I3 — this must never be swallowed into a
    silent pass or a silently-blanked field."""


def _default_call_fn(provider: str, model: str):
    """Same one-retry-after-a-pause discipline as tailor.py's tailor_job()
    (see tailor.py's own 429/503 handling) -- that retry lived only in the
    job-tailoring caller, not in _call_gemini/_call_groq themselves, so
    calling them directly here (as every function in this module does)
    skipped it entirely. A batch of even one claim's 10 questions can mean
    up to 20 calls (10 questions x up to 2 attempts each for I3
    regeneration) fired back to back with no pacing at all -- exactly what
    trips the free-tier rate limit, confirmed live against a real 429."""
    call = FREE_PROVIDERS[provider]

    def _call(prompt: str) -> str:
        try:
            return call(prompt, model)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (429, 503):
                time.sleep(20)
                return call(prompt, model)
            raise
    return _call


def resolve_free_call_fn(config: dict | None = None):
    """Pick a free-tier provider/model from config.yaml's `interview` block,
    falling back to gemini (the provider this repo already has a live key
    for) if unset. Never resolves to anthropic — that's a paid model in this
    repo's config, not a free tier, regardless of what tailor.provider says."""
    icfg = (config or {}).get("interview", {})
    provider = icfg.get("llm_provider", "gemini")
    if provider not in FREE_PROVIDERS:
        provider = "gemini"
    model_key = f"{provider}_model"
    tcfg = (config or {}).get("tailor", {})
    model = icfg.get(model_key) or tcfg.get(model_key) or (
        "gemini-flash-lite-latest" if provider == "gemini" else "llama-3.3-70b-versatile")
    return _default_call_fn(provider, model)


STORY_DRAFT_PROMPT = """You are drafting a FIRST-PASS interview story from a
single resume bullet, written as the CANDIDATE would actually say it out
loud in an interview. Use ONLY the facts given below. Do not invent any
number, metric, team size, stakeholder name, or outcome that isn't already
stated. Where the narrative needs a detail that isn't given, write the exact
literal placeholder text "[YOU FILL: <what's missing>]" instead of guessing
-- never write a plausible-sounding example value.

Write in FIRST PERSON, ACTIVE VOICE ("I led...", "I partnered with...",
never "was led" or "were managed") -- this is spoken practice material, not
a resume bullet restated in the passive voice. Active voice is not a new
fact and never conflicts with the no-invention rule: "I led the platform
redesign" asserts the same ownership the original bullet already claims,
just in a sayable sentence.

LANGUAGE STYLE: write the way this candidate would actually SAY it out loud,
not the way a resume or an AI assistant would write it. Plain, direct,
conversational sentences -- the kind a person naturally says, not essay
prose. No corporate jargon or buzzwords ("leverage," "utilize," "synergy,"
"spearheaded," "delve," "facilitate," "robust," "seamless," "streamline" as
a verb). No AI-cliche filler ("it's worth noting," "in today's fast-paced
environment," "this demonstrates my ability to..."). Short sentences over
long compound ones. Contractions are fine ("I'm," "didn't," "we'd").

RESUME BULLET: {claim_text}
COMPANY: {company}
ROLE: {role}

Return ONLY a JSON object with exactly these keys, no markdown fences, no
commentary:
{{
  "situation": "1-2 sentences of context, first person, using only the facts above",
  "task": "1 sentence, first person: what I needed to make happen",
  "action": "1-2 sentences, first person active voice: what I did, per the bullet -- use [YOU FILL: ...] for any step-level detail not stated",
  "result": "1 sentence, using only metrics/outcomes literally present in the bullet above",
  "reflection": "1 sentence on what this demonstrates -- no new facts, just framing"
}}"""

STRICTER_REMINDER = """

Your previous attempt included a number or fact not present in the bullet
above. Every number in your response MUST already appear in the bullet
text. Anything else, including team size, must be the literal placeholder
"[YOU FILL: ...]". Keep first-person, active voice."""


def generate_story_draft(claim: dict, master_resume: dict, config: dict | None = None,
                         call_fn=None) -> dict:
    """Draft SITAR fields from a single ResumeClaim. Returns
    {"ok": bool, "fields": dict|None, "violations": list, "regenerated": bool}.
    Raises InterviewFactIntegrityError if a violation survives regeneration --
    this is I3's hard-failure path, not a value the caller silently ignores."""
    call_fn = call_fn or resolve_free_call_fn(config)
    prompt = STORY_DRAFT_PROMPT.format(
        claim_text=claim["claim_text"],
        company=claim.get("source_company") or "unspecified",
        role=claim.get("source_role") or "unspecified",
    )

    required = ("situation", "task", "action", "result", "reflection")

    def _complete(f):
        return all(f.get(k) for k in required)

    fields = tailor._extract_json(call_fn(prompt))
    ok, violations = check_fact_integrity(fields, master_resume)
    ok = ok and _complete(fields)
    if not ok and not violations:
        violations = ["response missing one or more required SITAR fields"]
    regenerated = False
    if not ok:
        regenerated = True
        fields = tailor._extract_json(call_fn(prompt + STRICTER_REMINDER))
        ok, violations = check_fact_integrity(fields, master_resume)
        ok = ok and _complete(fields)
        if not ok and not violations:
            violations = ["response missing one or more required SITAR fields"]

    if not ok:
        raise InterviewFactIntegrityError(
            f"story draft still invalid after regeneration: {violations}")

    return {"ok": True, "fields": fields, "violations": [], "regenerated": regenerated}


TOPIC_RATIONALE_PROMPT = """A candidate preparing for a job interview has
this prep topic on their list:

"{topic}"

In ONE sentence, explain why an interviewer would actually probe this and
what a weak vs. strong answer signals to them. Do not introduce any new
number, statistic, or specific fact beyond what's already in the topic text
above -- reason about it generically. Return ONLY the sentence, no quotes,
no markdown, no preamble."""

STRICTER_TOPIC_REMINDER = """

Your previous attempt included a number not present in the topic text
above. Reason generically -- do not introduce any new statistic or figure."""


def generate_topic_rationale(topic_text: str, config: dict | None = None,
                             call_fn=None) -> dict:
    """One-sentence 'why this matters' framing for a prep topic. Lower fact
    risk than story drafting (it reasons about the topic generically, never
    a candidate-specific number), but still guarded: any digit in the
    output that isn't already present in topic_text itself is treated as an
    invented fact, since topic_text is the only ground truth this function
    is given. Same I3 discipline: one regeneration, then a hard failure."""
    call_fn = call_fn or resolve_free_call_fn(config)
    allowed_numbers = set(re.findall(r"\d+(?:\.\d+)?", topic_text))

    def _violations(text):
        found = set(re.findall(r"\d+(?:\.\d+)?", text))
        extra = found - allowed_numbers
        return [f"introduced number(s) not present in the topic: {sorted(extra)}"] if extra else []

    prompt = TOPIC_RATIONALE_PROMPT.format(topic=topic_text)
    rationale = call_fn(prompt).strip().strip('"')
    violations = _violations(rationale) if rationale else ["empty response"]
    regenerated = False
    if violations:
        regenerated = True
        rationale = call_fn(prompt + STRICTER_TOPIC_REMINDER).strip().strip('"')
        violations = _violations(rationale) if rationale else ["empty response"]

    if violations:
        raise InterviewFactIntegrityError(
            f"topic rationale still invalid after regeneration: {violations}")

    return {"ok": True, "rationale": rationale, "regenerated": regenerated}


ANSWER_DRAFT_PROMPT = """You are drafting a COMPLETE first-pass interview
answer for a specific question, for the candidate to read and edit -- not a
skeleton, a real draft with structure and reasoning fully worked out.

QUESTION: {question_text}
WHAT IT'S TESTING (the resume claim behind this question): {claim_text}
COMPANY: {company}

CONFIRMED FACTS AVAILABLE -- use ONLY these numbers/facts, invent nothing else:
{facts_block}

RELEVANT STORY FROM THE BANK (weave in naturally if it fits, ignore if not relevant):
{story_block}

RULES:
- The claim above already describes REAL actions the candidate took --
  treat its specific verbs and details as content to draft FROM, not just
  background to reference. If it says "partnered with UI/UX designers",
  your answer states that directly as the candidate's own action -- that is
  not a gap, it's already a fact.
- Use ONLY the facts given above -- the claim, the confirmed facts, and the
  story. Never invent a number, team size, stakeholder name, or outcome
  that isn't already given.
- Only use "[YOU FILL: <what's missing>]" for a detail genuinely absent
  from everything given (e.g. an exact team size, a named stakeholder, a
  precise date) -- not for something the claim already implies. Placeholder
  as little as the facts genuinely allow; an answer with more than 1-2
  placeholders usually means you're under-using the claim text given.
- Write in first person, active voice, throughout -- never "we" when the
  claim describes the candidate's own action, never third-person distance
  ("my role was to..." when you can just say what was done).
- Structure and reasoning should be FULLY drafted -- that part can and
  should be complete even when a specific number can't be.
- LANGUAGE STYLE: write the way this candidate would actually SAY it out
  loud in the room, not the way a resume or an AI assistant would write it.
  Plain, direct, conversational sentences a real person naturally says --
  not essay prose. No corporate jargon or buzzwords ("leverage," "utilize,"
  "synergy," "spearheaded," "delve," "facilitate," "robust," "seamless,"
  "streamline" as a verb). No AI-cliche filler ("it's worth noting," "in
  today's fast-paced environment," "this demonstrates my ability to...").
  Short sentences over long compound ones. Contractions are fine ("I'm,"
  "didn't," "we'd"). If a sentence sounds like it belongs in a corporate
  blog post, rewrite it the way you'd actually say it to a person.

Return ONLY a JSON object with exactly these keys, no markdown fences, no
commentary:
{{"answer_text": "the full drafted answer", "gaps": ["short description of each [YOU FILL: ...] marker used, empty list if none"]}}"""

ANSWER_STRICTER_REMINDER = """

Your previous attempt included a number or fact not in the confirmed facts
list above. Every number in your response MUST already appear in the facts
given. Anything else must be the literal placeholder "[YOU FILL: ...]"."""


def generate_answer_draft(question_text: str, claim_text: str, company: str,
                          story_text: str, master_resume: dict,
                          extra_allowed_numbers: set[str] | None = None,
                          config: dict | None = None, call_fn=None) -> dict:
    """Answer Bank §4 batch-generation primitive. Same I3 discipline as
    generate_story_draft(): one regeneration on a fact violation, then a
    hard failure. `extra_allowed_numbers` lets an already-confirmed
    fact_ledger entry for this process count as legitimate (it's a real
    candidate_asserted fact, just not resume-sourced)."""
    call_fn = call_fn or resolve_free_call_fn(config)
    facts_block = "\n".join(f"- {n}" for n in sorted(_all_resume_numbers(master_resume))) or "(none)"
    if extra_allowed_numbers:
        facts_block += "\n" + "\n".join(f"- {n} (confirmed by candidate)" for n in sorted(extra_allowed_numbers))
    prompt = ANSWER_DRAFT_PROMPT.format(
        question_text=question_text, claim_text=claim_text or "(none)",
        company=company or "unspecified", facts_block=facts_block,
        story_block=story_text or "(none)")

    def _parse_and_check(raw):
        parsed = tailor._extract_json(raw)
        answer_text = parsed.get("answer_text", "") or ""
        # Reuse check_fact_integrity's number-grounding discipline against a
        # single free-text field, same as the story path.
        ok, violations = check_fact_integrity(
            {"situation": answer_text}, master_resume, extra_allowed_numbers)
        if answer_text.strip() and ok:
            return answer_text, [], True
        if not answer_text.strip():
            return answer_text, ["empty response"], False
        return answer_text, violations, False

    answer_text, violations, ok = _parse_and_check(call_fn(prompt))
    regenerated = False
    if not ok:
        regenerated = True
        answer_text, violations, ok = _parse_and_check(call_fn(prompt + ANSWER_STRICTER_REMINDER))

    if not ok:
        raise InterviewFactIntegrityError(
            f"answer draft still invalid after regeneration: {violations}")

    return {"answer_text": answer_text, "regenerated": regenerated}


CRITIQUE_PROMPT = """You are critiquing a candidate's own written interview
answer, in this exact format (T§5.5): OBSERVATION (quote their own words),
WHY IT MATTERS (what an interviewer infers from that), HOW TO IMPROVE (one
specific structural change, never vague advice like "add more detail").

QUESTION: {question_text}
CANDIDATE'S ANSWER:
{answer_text}

Critique the answer as WRITTEN -- do not rewrite it, do not suggest facts
the candidate didn't give you, do not invent what a "better" version would
say. Your OBSERVATION must include a short verbatim quote from the answer
above (not a paraphrase).

Return ONLY a JSON object with exactly these keys, no markdown fences, no
commentary:
{{"observation": "...", "why_it_matters": "...", "how_to_improve": "..."}}"""


def critique_answer(question_text: str, answer_text: str, config: dict | None = None,
                    call_fn=None) -> dict:
    """T§5.5 feedback format, critique-only (E§4's "critique-only" mode) --
    never rewrites the answer, never invents what the candidate should have
    said. Lower-stakes than generation: this is transient feedback shown to
    the candidate, never stored as a fact, so it gets a lighter check than
    I3's full regenerate-then-hard-fail -- just a verbatim-quote check, with
    a `quote_verified` flag surfaced rather than a raised exception, since a
    slightly-off quote is a quality issue here, not a fabricated fact about
    the candidate."""
    call_fn = call_fn or resolve_free_call_fn(config)
    prompt = CRITIQUE_PROMPT.format(question_text=question_text, answer_text=answer_text)
    parsed = tailor._extract_json(call_fn(prompt))
    observation = parsed.get("observation", "") or ""

    quote_verified = False
    for m in re.finditer(r'"([^"]{8,})"', observation):
        if m.group(1) in answer_text:
            quote_verified = True
            break
    if not quote_verified:
        # No quoted span found via straight substring match — fall back to a
        # looser check (a long word sequence shared with the answer), since
        # models don't always wrap the quote in literal quote marks.
        words = re.findall(r"\w+", answer_text)
        for i in range(len(words) - 5):
            phrase = " ".join(words[i:i + 6])
            if phrase and phrase.lower() in observation.lower():
                quote_verified = True
                break

    return {
        "observation": observation,
        "why_it_matters": parsed.get("why_it_matters", ""),
        "how_to_improve": parsed.get("how_to_improve", ""),
        "quote_verified": quote_verified,
    }
