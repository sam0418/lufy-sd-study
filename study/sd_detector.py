"""Self-disclosure detector: the E033 v2 detector, ported verbatim.

Same model, same batch size, same prompt text (the "detect" prompt, the 19
Dou et al. category hints and the "self_disclosure" task), same input: the
user's utterance alone, without the chatbot line before it. E033 v2 was
frozen on 2026-11-21 (blind test set: precision 0.55, recall 0.75, kappa 0.59).
Per the 11/25 meeting it is not re-validated here; it is a fixed component.

Do not edit the prompt strings below: any change makes it a different detector.
"""
import json
import logging
import os
import re

from openai import OpenAI

log = logging.getLogger(__name__)

MODEL = os.environ.get("SD_MODEL", "gpt-4o-2024-08-06")
BATCH = 20

# Dou et al. (ACL 2024) Table 1, exactly; one hint each.
DOU_CATEGORIES = [
    "LOCATION", "AGE", "RELATIONSHIP STATUS", "AGE/GENDER", "PET", "APPEARANCE", "HUSBAND/BF",
    "WIFE/GF", "GENDER", "RACE/NATIONALITY", "SEXUAL ORIENTATION", "NAME", "CONTACT",
    "HEALTH", "FAMILY", "OCCUPATION", "MENTAL HEALTH", "EDUCATION", "FINANCE",
]
HINTS = {
    "LOCATION": "where the person lives or is, at city level or finer",
    "AGE": "age or age range",
    "AGE/GENDER": "combined shorthand like 20F",
    "GENDER": "gender",
    "RACE/NATIONALITY": "race, ethnicity or nationality",
    "RELATIONSHIP STATUS": "single, dating, married, divorced, broken up",
    "HUSBAND/BF": "details about a husband or boyfriend",
    "WIFE/GF": "details about a wife or girlfriend",
    "SEXUAL ORIENTATION": "sexual orientation, including hints like coming out or queer groups",
    "NAME": "a real name",
    "CONTACT": "phone number, email, handle, address",
    "PET": "owning a pet",
    "APPEARANCE": "height, weight, looks",
    "HEALTH": "physical illness, injury, medication, treatment, disability",
    "MENTAL HEALTH": "mental health conditions, therapy, psychiatric medication, mood disorders",
    "FAMILY": "family members and family situations",
    "OCCUPATION": "job or workplace",
    "EDUCATION": "school, degree, field of study",
    "FINANCE": "income, savings, debt, loans, investments, money trouble",
}

TASK = ('''Each text is something the user said in a casual chat. For each, list every category of '''
        '''personal information the speaker explicitly states about themselves or a close relation '''
        '''(a family member, partner or pet). Count only facts about the speaker's own life. Studies, '''
        '''exams, jobs and family members the speaker talks about as part of their own life do count. '''
        '''Not a disclosure: places, products, games, topics or people that are only mentioned, '''
        '''discussed or asked about without saying how they belong to the speaker's life; addressing '''
        '''the chat partner by name; opinions about the world; questions. Count LOCATION only if the '''
        '''speaker says they live, are, come from or have been somewhere specific (a city, country, '''
        '''address or landmark).''')

PROMPT = '''{task}

Categories (Dou et al., 2024):
{categories}

Texts:
{texts}

Return JSON only: {{"results": [{{"i": <index>, "categories": ["CATEGORY", ...]}}]}} with one entry per text; use [] when none apply.'''

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(max_retries=8, timeout=60)
    return _client


def _parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def build_prompt(texts):
    return PROMPT.format(
        task=TASK,
        categories="\n".join(f"- {c}: {HINTS[c]}" for c in DOU_CATEGORIES),
        texts="\n".join(f"{i}. {t}" for i, t in enumerate(texts)))


def _detect(texts):
    """One call for up to BATCH texts; a set of categories per text, None where the reply missed it."""
    try:
        res = _get_client().chat.completions.create(
            model=MODEL, temperature=0.0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content": build_prompt(texts)}])
        data = _parse_json(res.choices[0].message.content) or {}
    except Exception as e:
        log.error("SD detector call failed: %s", type(e).__name__)
        return [None] * len(texts)
    out = [None] * len(texts)
    for r in data.get("results", []):
        try:
            i = int(r["i"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < len(texts) and isinstance(r.get("categories"), list):
            out[i] = {c for c in r["categories"] if c in DOU_CATEGORIES}
    return out


def detect_batch(texts):
    """Categories per user utterance, in batches of 20 as in E033.

    Returns one dict per text: {"sd": bool, "categories": [...], "model": str},
    plus "error" when no verdict came back after one retry (then sd=False).
    """
    results = []
    for start in range(0, len(texts), BATCH):
        chunk = texts[start:start + BATCH]
        cats = _detect(chunk)
        missing = [i for i, c in enumerate(cats) if c is None]
        if missing:  # retry only the texts the reply missed, once
            again = _detect([chunk[i] for i in missing])
            for i, c in zip(missing, again):
                cats[i] = c
        for c in cats:
            if c is None:
                results.append({"sd": False, "categories": [], "model": MODEL, "error": "no_verdict"})
            else:
                results.append({"sd": bool(c), "categories": sorted(c), "model": MODEL})
    return results
