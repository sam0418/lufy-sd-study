"""Self-disclosure detector.

PROVISIONAL: categories, hints and task wording are copied from LUFY-EVO
evaluation/dou_signal.py (Dou et al., 2024), made speaker-only. The detector
shown on 11/25 (E033) has not been ported yet; when it is, replace
PROMPT/TASK here and keep the return shape:

    {"sd": bool, "categories": [str, ...], "model": str}

Per the 11/25 meeting the detector is not validated in this study; it is a
fixed component and must be described as such.
"""
import json
import os
import re

from openai import OpenAI

MODEL = os.environ.get("SD_MODEL", "gpt-4o")

DOU_CATEGORIES = [
    "LOCATION", "AGE", "RELATIONSHIP STATUS", "AGE/GENDER", "PET", "APPEARANCE", "HUSBAND/BF",
    "WIFE/GF", "GENDER", "RACE/NATIONALITY", "SEXUAL ORIENTATION", "NAME", "CONTACT",
    "HEALTH", "FAMILY", "OCCUPATION", "MENTAL HEALTH", "EDUCATION", "FINANCE",
]
HINTS = {
    "LOCATION": "where the person lives or is, at city level or finer",
    "AGE": "age or age range", "AGE/GENDER": "combined shorthand like 20F",
    "GENDER": "gender", "RACE/NATIONALITY": "race, ethnicity or nationality",
    "RELATIONSHIP STATUS": "single, dating, married, divorced, broken up",
    "HUSBAND/BF": "details about a husband or boyfriend", "WIFE/GF": "details about a wife or girlfriend",
    "SEXUAL ORIENTATION": "sexual orientation, including hints like coming out or queer groups",
    "NAME": "a real name", "CONTACT": "phone number, email, handle, address",
    "PET": "owning a pet", "APPEARANCE": "height, weight, looks",
    "HEALTH": "physical illness, injury, medication, treatment, disability",
    "MENTAL HEALTH": "mental health conditions, therapy, psychiatric medication, mood disorders",
    "FAMILY": "family members and family situations", "OCCUPATION": "job or workplace",
    "EDUCATION": "school, degree, field of study", "FINANCE": "income, savings, debt, loans, investments, money trouble",
}
# Speaker-only: label what the USER reveals about themselves (the earlier
# "second layer", which scored better than labelling both sides).
TASK = ("List every category of personal information the user's message reveals about the user "
        "themself, explicitly or by clear implication (a drug name implies a health condition). "
        "Interest in a food, culture, language, place or activity is not by itself a disclosure of "
        "RACE/NATIONALITY, LOCATION or any other category: \"loves Korean recipes\" reveals nothing "
        "about who the person is. The chatbot line is context only; never label it.")
PROMPT = """{task}

Categories (Dou et al., 2024):
{categories}

Chatbot line (context only): {context}
User message: {utterance}

Return JSON only: {{"categories": ["CATEGORY", ...]}}; use [] when none apply."""

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(max_retries=8, timeout=60)
    return _client


def detect_self_disclosure(utterance, context=""):
    prompt = PROMPT.format(
        task=TASK,
        categories="\n".join("- {}: {}".format(c, HINTS[c]) for c in DOU_CATEGORIES),
        context=context.replace("\n", " "), utterance=utterance.replace("\n", " "))
    for _ in range(3):
        resp = _get_client().chat.completions.create(
            model=MODEL, temperature=0.0, messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"})
        try:
            data = json.loads(re.search(r"\{.*\}", resp.choices[0].message.content or "", re.S).group(0))
        except (AttributeError, ValueError):
            continue
        cats = [c for c in data.get("categories", []) if c in DOU_CATEGORIES]
        return {"sd": bool(cats), "categories": cats, "model": MODEL}
    return {"sd": False, "categories": [], "model": MODEL, "error": "unparseable"}
