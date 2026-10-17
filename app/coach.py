"""AI accent coach: Gemini listens to the reference and your take side by side and
explains, in plain language, exactly what to change and how."""
import base64
import json
import re

import httpx

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class CoachError(Exception):
    pass


PROMPT = """You are an expert American-accent coach for voiceover artists. Your student is a native Hindi/Indian-English speaker who narrates YouTube videos for a US audience and wants to sound like a natural General American narrator.

You will hear two recordings of the same sentence:
1. REFERENCE — an American narrator saying it the target way.
2. STUDENT — the student's attempt.

Sentence: "{text}"
{extra}
Listen carefully to the actual sounds, not just the words. Compare the STUDENT to the REFERENCE and coach them like a friendly, direct human coach would in a one-on-one session.

Focus on what most makes the student sound Indian rather than American, for example: retroflex t/d, v/w mix-ups, "th" said as t/d, r sounds, flat or un-glided vowels (o, a, e), every syllable given equal weight instead of reducing unstressed vowels to "uh", wrong word stress, flat or sing-song melody, sentence endings that rise instead of fall, choppy rhythm.

Rules:
- Pick the 2 to 4 changes that would make the BIGGEST difference. Ignore tiny things.
- Write sounds in plain English respelling that anyone can read, with the stressed syllable in CAPITALS (e.g. "PUZ-uhl", "LAYT", "uh-PROACH"). Never use IPA symbols.
- For each fix, say exactly what to do with the mouth, tongue, lips, breath or pitch.
- Keep each field short and concrete. No generic advice like "practice more".
- If the take is already very close, say so and give one polish point.
- If the student's recording is silent, cut off, or a different sentence, say that in "verdict" and leave "fixes" empty.

Reply with ONLY a JSON object, no markdown, in exactly this shape:
{{
  "american_score": <integer 0-100, how natural-American the student's take sounds>,
  "verdict": "<one or two sentences: overall impression, honest and encouraging>",
  "good": "<one thing the student did well>",
  "fixes": [
    {{
      "word": "<the exact word or short phrase from the sentence>",
      "type": "<sound | stress | melody | rhythm>",
      "you_said": "<how the student said it, plain respelling or description>",
      "american": "<how the reference / an American says it, plain respelling>",
      "how": "<1-2 sentences: exactly what to physically do differently>"
    }}
  ],
  "next_take": "<one sentence: the single thing to focus on in the very next attempt>"
}}"""


def _find_text(node):
    if isinstance(node, dict):
        if isinstance(node.get("output_text"), str) and node["output_text"].strip():
            return node["output_text"]
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            return node["text"]
        for k in ("steps", "outputs", "content", "candidates", "parts"):
            if k in node:
                t = _find_text(node[k])
                if t:
                    return t
    elif isinstance(node, list):
        # last model text wins (skip the echoed user input)
        for v in reversed(node):
            if isinstance(v, dict) and v.get("type") in ("user_input",):
                continue
            t = _find_text(v)
            if t:
                return t
    return None


def _parse(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise CoachError("The coach didn't return a readable answer. Try the take again.")
    data = json.loads(m.group(0))
    fixes = []
    for f in (data.get("fixes") or [])[:4]:
        if isinstance(f, dict) and f.get("word"):
            fixes.append({k: str(f.get(k, "")).strip() for k in ("word", "type", "you_said", "american", "how")})
    score = data.get("american_score")
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = None
    return {"american_score": score, "verdict": str(data.get("verdict", "")).strip(),
            "good": str(data.get("good", "")).strip(), "fixes": fixes,
            "next_take": str(data.get("next_take", "")).strip()}


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def coach(ref_wav16, take_wav16, text, key, model="gemini-3.8-flash", hints=None):
    if not key:
        raise CoachError("Add your Gemini API key in Settings to turn on the coach.")
    extra = ""
    if hints:
        extra = "\nAn automatic pronunciation scorer also flagged these (use them only if you agree after listening):\n- " + "\n- ".join(hints[:6]) + "\n"
    body = {"model": model or "gemini-3.8-flash",
            "input": [
                {"type": "text", "text": PROMPT.format(text=text.replace('"', "'"), extra=extra)},
                {"type": "text", "text": "REFERENCE recording:"},
                {"type": "audio", "data": _b64(ref_wav16), "mime_type": "audio/wav"},
                {"type": "text", "text": "STUDENT recording:"},
                {"type": "audio", "data": _b64(take_wav16), "mime_type": "audio/wav"},
            ]}
    r = httpx.post(f"{GEMINI_BASE}/interactions",
                   headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                   json=body, timeout=120)
    if r.status_code == 429:
        raise CoachError("The Gemini free-tier limit is used up for now. The coach will work again after it resets.")
    if r.status_code != 200:
        raise CoachError(f"Coach error {r.status_code}: {r.text[:300]}")
    text_out = _find_text(r.json())
    if not text_out:
        raise CoachError("The coach returned an empty answer. Try the take again.")
    try:
        return _parse(text_out)
    except json.JSONDecodeError:
        raise CoachError("The coach's answer couldn't be read. Try the take again.")
