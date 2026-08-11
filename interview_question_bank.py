"""Base question bank — the real gap this session's claim-derived tree never
covered. T§4.4's question tree and T§4.5's metrics defense only ever
generate questions FROM a resume bullet, so there was no "Tell me about
yourself," no PM-fundamentals, no behavioral question, no product-sense
case unless a bullet happened to map to one. This module is a curated,
static list of standard high-probability PM interview questions -- not
LLM-generated (there's nothing to generate: these are well-known, common
questions, and a static list is free, deterministic, and testable, the
same reasoning that kept the rest of Phase 1 off the LLM).

Deliberately excludes two categories from the original prompt's list:
"Current Project Deep Dives" and "Resume Claim Defense" are already fully
covered by interview_prep.py's claim_question tree and metric_defense set
respectively -- duplicating them here as generic questions would be a
strictly worse version of what the claim-specific tree already does.

~90 questions across 10 categories, not padded to a fixed 100-120 --
quality and genuine distinctness over hitting a round number.
"""
from __future__ import annotations

CATEGORY_LABELS = {
    "intro_career": "Introduction & Career",
    "current_company_role": "Current Company & Role",
    "pm_fundamentals": "PM Fundamentals",
    "product_sense_case": "Product Sense / Cases",
    "metrics_analytics": "Metrics & Analytics",
    "stakeholder_management": "Stakeholder Management",
    "execution": "Execution & Delivery",
    "behavioral": "Behavioral",
    "target_company_role": "Target Company & Role",
    "hr_closing": "HR / Closing",
}

# tags let relevance scoring match a question to a candidate/process without
# an LLM call -- e.g. a question tagged "fintech" scores higher when the
# target JD/company text mentions lending/fintech/NBFC.
BASE_QUESTIONS = [
    # ---------------------------------------------------- intro_career (10)
    {"id": 1, "category": "intro_career", "text": "Tell me about yourself.", "tags": []},
    {"id": 2, "category": "intro_career", "text": "Walk me through your resume.", "tags": []},
    {"id": 3, "category": "intro_career", "text": "Why Product Management?", "tags": []},
    {"id": 4, "category": "intro_career", "text": "Why this industry?", "tags": ["fintech", "lending"]},
    {"id": 5, "category": "intro_career", "text": "Why are you looking for a change now?", "tags": []},
    {"id": 6, "category": "intro_career", "text": "What are your strengths?", "tags": []},
    {"id": 7, "category": "intro_career", "text": "What is your biggest weakness?", "tags": []},
    {"id": 8, "category": "intro_career", "text": "What are you looking for in your next role?", "tags": []},
    {"id": 9, "category": "intro_career", "text": "How would your manager describe you?", "tags": []},
    {"id": 10, "category": "intro_career", "text": "Where do you see yourself in 3-5 years?", "tags": []},

    # ------------------------------------------------ current_company_role (8)
    {"id": 11, "category": "current_company_role", "text": "Tell me about your current role.", "tags": []},
    {"id": 12, "category": "current_company_role", "text": "What exactly do you own day to day?", "tags": []},
    {"id": 13, "category": "current_company_role", "text": "What metrics do you personally track?", "tags": []},
    {"id": 14, "category": "current_company_role", "text": "What are the biggest problems in your current product?", "tags": []},
    {"id": 15, "category": "current_company_role", "text": "How do you prioritize your roadmap?", "tags": []},
    {"id": 16, "category": "current_company_role", "text": "Who do you work with cross-functionally, and how?", "tags": []},
    {"id": 17, "category": "current_company_role", "text": "What's the most impactful thing you've shipped there?", "tags": []},
    {"id": 18, "category": "current_company_role", "text": "If you had another quarter there, what would you fix?", "tags": []},

    # -------------------------------------------------------- pm_fundamentals (10)
    {"id": 19, "category": "pm_fundamentals", "text": "What does a Product Manager actually do?", "tags": []},
    {"id": 20, "category": "pm_fundamentals", "text": "How do you identify a real problem worth solving?", "tags": []},
    {"id": 21, "category": "pm_fundamentals", "text": "How do you prioritize a backlog with competing demands?", "tags": []},
    {"id": 22, "category": "pm_fundamentals", "text": "How do you build a product roadmap?", "tags": []},
    {"id": 23, "category": "pm_fundamentals", "text": "How do you define success for a feature?", "tags": []},
    {"id": 24, "category": "pm_fundamentals", "text": "How do you validate a product idea before building it?", "tags": []},
    {"id": 25, "category": "pm_fundamentals", "text": "How do you balance business needs against customer needs?", "tags": []},
    {"id": 26, "category": "pm_fundamentals", "text": "How do you say no to a stakeholder, well?", "tags": []},
    {"id": 27, "category": "pm_fundamentals", "text": "How do you operate under genuine ambiguity?", "tags": []},
    {"id": 28, "category": "pm_fundamentals", "text": "What separates a good PM from a great one?", "tags": []},

    # -------------------------------------------------------- product_sense_case (7)
    {"id": 29, "category": "product_sense_case",
     "text": "Loan application drop-off just spiked 15% week over week. Walk me through how you'd diagnose it.",
     "tags": ["fintech", "lending"]},
    {"id": 30, "category": "product_sense_case",
     "text": "Design a digital onboarding flow for a first-time home loan applicant.",
     "tags": ["fintech", "lending", "home loan"]},
    {"id": 31, "category": "product_sense_case",
     "text": "How would you improve conversion on an existing loan product without changing eligibility criteria?",
     "tags": ["fintech", "lending"]},
    {"id": 32, "category": "product_sense_case",
     "text": "Design a system that recommends an alternate product when a customer fails eligibility for the one they applied for.",
     "tags": ["fintech", "lending", "recommendation"]},
    {"id": 33, "category": "product_sense_case",
     "text": "A partner's leads are converting at half the rate of your direct channel. What do you do?",
     "tags": ["fintech", "partnerships"]},
    {"id": 34, "category": "product_sense_case",
     "text": "Leads increased 30% this quarter, but lead quality declined. How do you respond?",
     "tags": ["fintech", "growth"]},
    {"id": 35, "category": "product_sense_case",
     "text": "Design a lending product for a customer segment your company doesn't currently serve well.",
     "tags": ["fintech", "lending"]},

    # -------------------------------------------------------- metrics_analytics (8)
    {"id": 36, "category": "metrics_analytics", "text": "What metrics do you track for your product, and why those?", "tags": []},
    {"id": 37, "category": "metrics_analytics", "text": "What would you pick as a North Star metric, and why?", "tags": []},
    {"id": 38, "category": "metrics_analytics", "text": "How do you measure acquisition quality, not just volume?", "tags": []},
    {"id": 39, "category": "metrics_analytics", "text": "Walk me through how you'd analyze a funnel with a sudden drop.", "tags": []},
    {"id": 40, "category": "metrics_analytics", "text": "Conversion dropped 10% overnight — how do you investigate?", "tags": []},
    {"id": 41, "category": "metrics_analytics", "text": "How do you prove a feature you shipped actually caused the impact you're claiming?", "tags": []},
    {"id": 42, "category": "metrics_analytics", "text": "What analytics tools have you actually used, and for what?", "tags": []},
    {"id": 43, "category": "metrics_analytics", "text": "How do you decide a metric movement is signal, not noise?", "tags": []},

    # ------------------------------------------------- stakeholder_management (7)
    {"id": 44, "category": "stakeholder_management", "text": "Tell me about a real stakeholder conflict you navigated.", "tags": []},
    {"id": 45, "category": "stakeholder_management", "text": "Tell me about a disagreement with Engineering.", "tags": []},
    {"id": 46, "category": "stakeholder_management", "text": "Tell me about a disagreement with the Business team.", "tags": []},
    {"id": 47, "category": "stakeholder_management", "text": "How do you prioritize when two stakeholders both say their ask is P0?", "tags": []},
    {"id": 48, "category": "stakeholder_management", "text": "How do you influence a decision when you have no formal authority?", "tags": []},
    {"id": 49, "category": "stakeholder_management", "text": "What do you do when Engineering pushes back on scope you believe in?", "tags": []},
    {"id": 50, "category": "stakeholder_management", "text": "What do you do when the business wants something your data says not to build?", "tags": []},

    # ------------------------------------------------------------ execution (7)
    {"id": 51, "category": "execution", "text": "How do you turn a vague business problem into concrete requirements?", "tags": []},
    {"id": 52, "category": "execution", "text": "Walk me through how you write a PRD.", "tags": []},
    {"id": 53, "category": "execution", "text": "How do you keep a project moving when it depends on another team's timeline?", "tags": []},
    {"id": 54, "category": "execution", "text": "How do you handle scope creep mid-sprint?", "tags": []},
    {"id": 55, "category": "execution", "text": "Tell me about a launch that didn't go as planned.", "tags": []},
    {"id": 56, "category": "execution", "text": "Tell me about a production issue you had to manage.", "tags": []},
    {"id": 57, "category": "execution", "text": "How do you decide what NOT to build?", "tags": []},

    # ------------------------------------------------------------ behavioral (10)
    {"id": 58, "category": "behavioral", "text": "Tell me about a time you failed.", "tags": []},
    {"id": 59, "category": "behavioral", "text": "Tell me about a mistake you made and how you handled it.", "tags": []},
    {"id": 60, "category": "behavioral", "text": "Tell me about a time you received difficult feedback.", "tags": []},
    {"id": 61, "category": "behavioral", "text": "Tell me about a conflict with a teammate.", "tags": []},
    {"id": 62, "category": "behavioral", "text": "Tell me about a time you influenced someone without authority.", "tags": []},
    {"id": 63, "category": "behavioral", "text": "Tell me about a time you were under real pressure.", "tags": []},
    {"id": 64, "category": "behavioral", "text": "Tell me about a time you had competing priorities and how you chose.", "tags": []},
    {"id": 65, "category": "behavioral", "text": "Tell me about a project that failed.", "tags": []},
    {"id": 66, "category": "behavioral", "text": "Tell me about the project you're proudest of, and why.", "tags": []},
    {"id": 67, "category": "behavioral", "text": "What's the biggest lesson your career has taught you so far?", "tags": []},

    # ------------------------------------------------- target_company_role (9)
    {"id": 68, "category": "target_company_role", "text": "Why do you want to join us specifically?", "tags": []},
    {"id": 69, "category": "target_company_role", "text": "Why this role, specifically?", "tags": []},
    {"id": 70, "category": "target_company_role", "text": "Why are you a strong fit for this?", "tags": []},
    {"id": 71, "category": "target_company_role", "text": "What from your current experience transfers directly to this role?", "tags": []},
    {"id": 72, "category": "target_company_role", "text": "What gaps do you think you have for this role, honestly?", "tags": []},
    {"id": 73, "category": "target_company_role", "text": "What would your first 90 days here look like?", "tags": []},
    {"id": 74, "category": "target_company_role", "text": "If you joined, what's the first thing you'd want to improve about our product?", "tags": []},
    {"id": 75, "category": "target_company_role", "text": "Who do you think our biggest competitors are?", "tags": []},
    {"id": 76, "category": "target_company_role", "text": "How would you compare our approach to your current company's?", "tags": []},

    # -------------------------------------------------------------- hr_closing (7)
    {"id": 77, "category": "hr_closing", "text": "What are your compensation expectations?", "tags": []},
    {"id": 78, "category": "hr_closing", "text": "What's your notice period, and is it flexible?", "tags": []},
    {"id": 79, "category": "hr_closing", "text": "Are you interviewing elsewhere?", "tags": []},
    {"id": 80, "category": "hr_closing", "text": "What would make you say yes to an offer from us?", "tags": []},
    {"id": 81, "category": "hr_closing", "text": "What would make you say no?", "tags": []},
    {"id": 82, "category": "hr_closing", "text": "Do you have any concerns about this role that we should address now?", "tags": []},
    {"id": 83, "category": "hr_closing", "text": "What questions do you have for us?", "tags": []},
]

CATEGORY_BASE_IMPORTANCE = {
    # Rough prior on how load-bearing a category typically is, independent
    # of any specific process -- combined with JD/company relevance at
    # scoring time, not used alone.
    "intro_career": 0.9, "current_company_role": 0.85, "pm_fundamentals": 0.7,
    "product_sense_case": 0.8, "metrics_analytics": 0.75, "stakeholder_management": 0.75,
    "execution": 0.65, "behavioral": 0.85, "target_company_role": 0.9, "hr_closing": 0.4,
}


# Per-question likelihood, for the questions whose real-world frequency is
# clearly not their category average. CATEGORY_BASE_IMPORTANCE alone is too
# coarse: it scored "Tell me about yourself" -- which opens essentially every
# interview ever conducted -- identically to "Where do you see yourself in
# 3-5 years", so a ranked plan could and did drop the opener entirely (found
# live on a 1-day-out plan). Anything not listed here falls back to its
# category prior; these are well-known frequencies, not model output.
QUESTION_LIKELIHOOD = {
    1: 0.98,   # Tell me about yourself
    2: 0.92,   # Walk me through your resume
    68: 0.95,  # Why do you want to join us specifically?
    69: 0.92,  # Why this role, specifically?
    83: 0.95,  # What questions do you have for us?
    5: 0.88,   # Why are you looking for a change now?
    11: 0.88,  # Tell me about your current role
    70: 0.88,  # Why are you a strong fit
    58: 0.85,  # Tell me about a time you failed
    72: 0.82,  # What gaps do you have, honestly
    17: 0.82,  # Most impactful thing you've shipped
    77: 0.80,  # Compensation expectations
    78: 0.78,  # Notice period
    # Genuinely less frequent than their category average implies.
    10: 0.45,  # Where do you see yourself in 3-5 years
    67: 0.45,  # Biggest lesson your career has taught you
    75: 0.50,  # Who are our biggest competitors
    76: 0.45,  # Compare our approach to your current company's
    81: 0.45,  # What would make you say no
    18: 0.45,  # If you had another quarter there
}


def question_likelihood(q: dict) -> float:
    """Per-question frequency where known, category prior otherwise."""
    explicit = QUESTION_LIKELIHOOD.get(q["id"])
    if explicit is not None:
        return explicit
    return CATEGORY_BASE_IMPORTANCE.get(q["category"], 0.5)


def relevant_questions(jd_text: str, role_title: str) -> list[dict]:
    """Every base question, tagged with whether its tags matched the JD/role
    text -- a tagged question scores higher but an untagged one (most
    intro/behavioral/fundamentals questions apply universally) is never
    excluded. No LLM call: this is lexical tag matching against static
    content, same discipline as the rest of Phase 1."""
    haystack = f"{jd_text or ''} {role_title or ''}".lower()
    out = []
    for q in BASE_QUESTIONS:
        tag_hit = any(t.lower() in haystack for t in q["tags"])
        out.append({**q, "tag_matched": tag_hit})
    return out
