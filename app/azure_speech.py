"""Azure Speech: pronunciation assessment (listens to the audio itself, sound by sound)
and word-timestamped transcription for YouTube reference speakers."""
import difflib
import json
import re
import threading

import azure.cognitiveservices.speech as sdk


class AzureError(Exception):
    pass


TICKS = 1e7  # Azure offsets/durations are in 100 ns ticks


def _norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower().replace("’", "'"))


def reference_words(text):
    return [w for w in (_norm(t) for t in text.split()) if w]


def _config(key, region):
    if not key:
        raise AzureError("Add your Azure Speech key in Settings to run the accent check.")
    cfg = sdk.SpeechConfig(subscription=key, region=region or "eastus")
    cfg.speech_recognition_language = "en-US"
    return cfg


def _run_continuous(recognizer, timeout):
    results, errors = [], []
    done = threading.Event()

    def on_recognized(evt):
        if evt.result.reason == sdk.ResultReason.RecognizedSpeech:
            raw = evt.result.properties.get(sdk.PropertyId.SpeechServiceResponse_JsonResult)
            if raw:
                results.append(json.loads(raw))

    def on_canceled(evt):
        d = evt.cancellation_details
        if d.reason == sdk.CancellationReason.Error:
            errors.append(f"{d.error_code}: {d.error_details}")
        done.set()

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(lambda e: done.set())
    recognizer.start_continuous_recognition()
    done.wait(timeout=timeout)
    recognizer.stop_continuous_recognition()
    if errors and not results:
        raise AzureError("Azure: " + errors[0])
    return results


def assess(wav16k_path, reference_text, key, region, audio_seconds=30):
    """Pronunciation assessment against an American English model.

    Returns {"scores": {...}, "words": [... one entry per reference word, in order ...],
             "insertions": [...]} with times in seconds.
    """
    cfg = _config(key, region)
    audio_cfg = sdk.audio.AudioConfig(filename=wav16k_path)
    pa = sdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=sdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=sdk.PronunciationAssessmentGranularity.Phoneme,
        enable_miscue=False)  # we align miscues ourselves across segments (more reliable)
    pa.phoneme_alphabet = "IPA"
    pa.nbest_phoneme_count = 5   # lets us say WHICH sound you actually produced
    pa.enable_prosody_assessment()
    rec = sdk.SpeechRecognizer(speech_config=cfg, audio_config=audio_cfg)
    pa.apply_to(rec)
    segments = _run_continuous(rec, timeout=audio_seconds * 2 + 30)
    return _normalise(segments, reference_text)


def _phonemes(w):
    out = []
    for p in w.get("Phonemes", []) or []:
        ppa = p.get("PronunciationAssessment", {}) or {}
        nbest = [(n.get("Phoneme"), n.get("Score")) for n in (ppa.get("NBestPhonemes") or [])]
        out.append({"ph": p.get("Phoneme"), "acc": ppa.get("AccuracyScore"),
                    "s": p.get("Offset", 0) / TICKS if p.get("Offset") is not None else None,
                    "e": (p.get("Offset", 0) + p.get("Duration", 0)) / TICKS if p.get("Offset") is not None else None,
                    "nbest": nbest})
    return out


def _syllables(w):
    out = []
    for sy in w.get("Syllables", []) or []:
        if sy.get("Offset") is None:
            continue
        out.append({"syl": sy.get("Syllable"), "text": sy.get("Grapheme") or sy.get("Syllable"),
                    "acc": (sy.get("PronunciationAssessment") or {}).get("AccuracyScore"),
                    "s": sy["Offset"] / TICKS, "e": (sy["Offset"] + sy.get("Duration", 0)) / TICKS})
    return out


def _normalise(segments, reference_text):
    recognized, flu, pros, seg_durs = [], [], [], []
    for seg in segments:
        nb = (seg.get("NBest") or [{}])[0]
        spa = nb.get("PronunciationAssessment") or {}
        d = seg.get("Duration", 0) / TICKS
        if spa.get("FluencyScore") is not None:
            flu.append((spa["FluencyScore"], d))
        if spa.get("ProsodyScore") is not None:
            pros.append((spa["ProsodyScore"], d))
        seg_durs.append(d)
        for w in nb.get("Words", []) or []:
            wpa = w.get("PronunciationAssessment") or {}
            fb = (wpa.get("Feedback") or {}).get("Prosody") or {}
            recognized.append({
                "word": w.get("Word", ""), "key": _norm(w.get("Word", "")),
                "acc": wpa.get("AccuracyScore"),
                "azure_error": wpa.get("ErrorType", "None"),
                "s": w["Offset"] / TICKS, "e": (w["Offset"] + w.get("Duration", 0)) / TICKS,
                "syllables": _syllables(w), "phonemes": _phonemes(w),
                "break_errors": ((fb.get("Break") or {}).get("ErrorTypes")) or [],
                "intonation_errors": ((fb.get("Intonation") or {}).get("ErrorTypes")) or [],
            })

    ref_tokens = [t for t in reference_text.split() if _norm(t)]
    ref_keys = [_norm(t) for t in ref_tokens]
    rec_keys = [r["key"] for r in recognized]
    words = [{"word": t, "error": "Omission", "acc": 0, "s": None, "e": None,
              "syllables": [], "phonemes": [], "break_errors": [], "intonation_errors": []}
             for t in ref_tokens]
    insertions = []
    sm = difflib.SequenceMatcher(a=ref_keys, b=rec_keys, autojunk=False)
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            for i, j in zip(range(a0, a1), range(b0, b1)):
                r = dict(recognized[j]); r["word"] = ref_tokens[i]
                acc = r.get("acc") or 0
                r["error"] = "Mispronunciation" if acc < 60 else "None"
                words[i] = r
        elif op == "replace":
            # a substituted word: keep timing of the recognised one but mark it mispronounced
            for k, i in enumerate(range(a0, a1)):
                j = b0 + k
                if j < b1:
                    r = dict(recognized[j]); r["heard"] = r["word"]; r["word"] = ref_tokens[i]
                    r["error"] = "Mispronunciation"
                    r["acc"] = min(r.get("acc") or 0, 40)
                    words[i] = r
            for j in range(b0 + (a1 - a0), b1):
                insertions.append(recognized[j])
        elif op == "insert":
            insertions.extend(recognized[b0:b1])

    said = [w for w in words if w["error"] != "Omission"]
    accuracy = sum((w.get("acc") or 0) for w in said) / len(said) if said else 0
    completeness = 100.0 * len(said) / len(words) if words else 0

    def wavg(pairs):
        tot = sum(d for _, d in pairs)
        return sum(v * d for v, d in pairs) / tot if tot else (sum(v for v, _ in pairs) / len(pairs) if pairs else None)

    fluency = wavg(flu)
    prosody = wavg(pros)
    parts = [(accuracy, 0.4), (completeness, 0.2)]
    if fluency is not None:
        parts.append((fluency, 0.2))
    if prosody is not None:
        parts.append((prosody, 0.2))
    overall = sum(v * wt for v, wt in parts) / sum(wt for _, wt in parts)
    return {"scores": {"overall": round(overall, 1), "accuracy": round(accuracy, 1),
                       "fluency": None if fluency is None else round(fluency, 1),
                       "completeness": round(completeness, 1),
                       "prosody": None if prosody is None else round(prosody, 1)},
            "words": words, "insertions": insertions}


def transcribe(wav16k_path, key, region, audio_seconds=600):
    """Word-timestamped transcription. Returns list of segments {text, s, e, words:[{word,s,e}]}."""
    cfg = _config(key, region)
    cfg.request_word_level_timestamps()
    cfg.output_format = sdk.OutputFormat.Detailed
    audio_cfg = sdk.audio.AudioConfig(filename=wav16k_path)
    rec = sdk.SpeechRecognizer(speech_config=cfg, audio_config=audio_cfg)
    segments = _run_continuous(rec, timeout=audio_seconds * 1.5 + 60)
    out = []
    for seg in segments:
        nb = (seg.get("NBest") or [{}])[0]
        words = [{"word": w.get("Word", ""), "s": w["Offset"] / TICKS,
                  "e": (w["Offset"] + w.get("Duration", 0)) / TICKS}
                 for w in nb.get("Words", []) or [] if w.get("Offset") is not None]
        if not words:
            continue
        out.append({"text": seg.get("DisplayText") or nb.get("Display") or " ".join(w["word"] for w in words),
                    "s": words[0]["s"], "e": words[-1]["e"], "words": words})
    return out
