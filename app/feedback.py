"""Turns the raw measurements into short, specific coaching notes."""

# Coaching tips for American sounds that Indian English speakers most often shift.
TIPS = {
    "v": "Top teeth rest lightly on your lower lip and the sound buzzes. Don't round your lips — that makes a “w”.",
    "w": "Round your lips into a small circle with no teeth touching, then open — “wet”, “we”.",
    "θ": "Tongue tip between your teeth and blow air — “think”, not “tink”.",
    "ð": "Tongue tip between your teeth with voice buzzing — “this”, not “dis”.",
    "t": "Tongue tip on the ridge behind your top teeth, not curled back. Between vowels Americans flap it to a quick “d”: water → “wah-der”.",
    "d": "Tongue tip on the ridge behind your top teeth, not curled back.",
    "ɾ": "Quick flapped “d” — “better” sounds like “bedder”.",
    "ɹ": "Don't tap or roll the r. Pull the tongue back without touching the roof, lips slightly rounded.",
    "ɝ": "Keep the r inside the vowel — “bird”, “work”, “her”. Don't drop it.",
    "ɚ": "Unstressed r-vowel: “water” ends in “-er” with the r fully there.",
    "æ": "Drop your jaw and spread your lips — “cat”, “bad”, “that”. It's wider than “e”.",
    "ɑ": "Open “ah” — American “hot”, “not”, “problem” use “ah”, not “o”.",
    "ɔ": "Open, rounded “aw” — “thought”, “call”.",
    "oʊ": "Glide it: “go” = “goh-oo”, not a flat “o”.",
    "eɪ": "Glide it: “day” = “deh-ee”, not a flat “e”.",
    "aɪ": "Full glide from “ah” to “ee” — “time”, “my”.",
    "aʊ": "Glide from “ah” to “oo” — “now”, “about”.",
    "ə": "Relax unstressed vowels into a quick, weak “uh”. Don't pronounce every vowel fully.",
    "ʌ": "Short relaxed “uh” — “cup”, “but”, “money”.",
    "ɪ": "Short relaxed vowel — “sit”, not “seat”.",
    "i": "Long, tense “ee” — “seat”, “people”.",
    "ʊ": "Short relaxed “oo” — “book”, “good”.",
    "u": "Long rounded “oo” — “food”, “you”.",
    "z": "Buzz it — “is”, “was”, “easy”. Don't turn it into “s” or “j”.",
    "ʒ": "Soft buzzing “zh” — “measure”, “usually”. Not “j”.",
    "dʒ": "“j” as in “just” — not “z”.",
    "ʃ": "“sh” with rounded lips — “should”, “nation”.",
    "l": "At the end of a word use a dark L: raise the back of your tongue — “feel”, “call”.",
    "p": "At the start of a stressed syllable add a puff of air — “people”, “power”.",
    "k": "At the start of a stressed syllable add a puff of air — “come”, “keep”.",
    "ŋ": "Back of the tongue on the soft palate; don't add a hard “g” — “sing-ing”, not “sing-ging”.",
    "h": "Breathy “h” — don't drop it.",
    "f": "Top teeth on lower lip, pure air — not “p”.",
}

KIND_LABEL = {"sound": "Sound", "stress": "Word stress", "melody": "Melody", "rhythm": "Rhythm",
              "missing": "Missing word", "style": "Target speaker"}


def phoneme_issues(assessment, limit=6):
    notes = []
    for i, w in enumerate(assessment.get("words", [])):
        if w["error"] == "Omission":
            notes.append({"kind": "missing", "sev": 5, "word": i, "text": f"“{w['word']}” wasn't heard. Say it clearly."})
            continue
        if w.get("heard"):
            notes.append({"kind": "sound", "sev": 5, "word": i, "text": f"“{w['word']}” was heard as “{w['heard']}”."})
        for p in w.get("phonemes", []):
            acc = p.get("acc")
            if acc is None or acc >= 60:
                continue
            exp = p.get("ph")
            alt = next((ph for ph, sc in p.get("nbest", []) if ph and ph != exp), None)
            heard = f", it came out closer to /{alt}/" if alt else ""
            tip = TIPS.get(exp, "")
            notes.append({"kind": "sound", "sev": 5 if acc < 40 else 4, "word": i, "phoneme": exp,
                          "text": f"In “{w['word']}” the /{exp}/ sound scored {acc:.0f}{heard}. {tip}".strip()})
        for be in w.get("break_errors", []) or []:
            if be == "UnexpectedBreak":
                notes.append({"kind": "rhythm", "sev": 2, "word": i, "text": f"Unexpected break before “{w['word']}”. Keep the phrase flowing."})
            elif be == "MissingBreak":
                notes.append({"kind": "rhythm", "sev": 1, "word": i, "text": f"A short pause is expected near “{w['word']}”."})
        if "Monotone" in (w.get("intonation_errors") or []):
            notes.append({"kind": "melody", "sev": 3, "word": i, "text": "Azure flagged this sentence as monotone. Let your pitch move on the important words."})
    # one monotone note is enough; also merge repeats of the same phoneme
    seen, uniq = set(), []
    for n in notes:
        key = (n["kind"], n.get("phoneme") or n["text"]) if n["kind"] != "melody" else ("mono",)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(n)
    return uniq


def style_notes(you_feats, profile, name):
    out = []
    if not profile:
        return out
    r, pr = you_feats.get("rate_wps"), profile.get("rate_wps")
    if r and pr:
        if r < pr * 0.85:
            out.append({"kind": "style", "sev": 2, "text": f"{name} talks at about {pr} words/sec; you're at {r}. Pick up the pace."})
        elif r > pr * 1.15:
            out.append({"kind": "style", "sev": 2, "text": f"{name} talks at about {pr} words/sec; you're at {r}. Slow down a little."})
    v, pv = you_feats.get("range_st"), profile.get("range_st")
    if v and pv:
        if v < pv * 0.75:
            out.append({"kind": "style", "sev": 2, "text": f"{name}'s voice moves across ~{pv:.1f} semitones; yours only {v:.1f}. Add more lift and fall."})
        elif v > pv * 1.35:
            out.append({"kind": "style", "sev": 1, "text": f"{name} is steadier (~{pv:.1f} semitones) than you ({v:.1f}). Calm the melody down."})
    return out


def build(assessment, comparison, you_feats=None, profile=None, profile_name=None, limit=7):
    notes = []
    if assessment:
        notes += phoneme_issues(assessment)
    if comparison:
        notes += comparison.get("findings", [])
    if you_feats and profile:
        notes += style_notes(you_feats, profile, profile_name or "Your target speaker")
    notes.sort(key=lambda n: -n["sev"])
    for n in notes:
        n["label"] = KIND_LABEL.get(n["kind"], n["kind"])
    top = notes[:limit]
    if not top and (assessment or comparison):
        top = [{"kind": "ok", "label": "Nice", "sev": 0, "text": "That take matches the reference closely. Keep it, or try for an even smoother read."}]
    return {"top": top, "all": notes}
