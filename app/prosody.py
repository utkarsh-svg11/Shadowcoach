"""Melody (pitch), stress and rhythm analysis taken straight from the audio signal."""
import math

import numpy as np
import parselmouth


def _track(path):
    snd = parselmouth.Sound(path)
    pitch = snd.to_pitch_ac(time_step=0.01, pitch_floor=65, pitch_ceiling=450)
    t = pitch.xs()
    f0 = pitch.selected_array["frequency"]
    inten = snd.to_intensity(minimum_pitch=75, time_step=0.01)
    return snd.get_total_duration(), t, f0, inten.xs(), inten.values[0]


def _win(t, v, a, b):
    m = (t >= a) & (t < b)
    x = v[m]
    return x[~np.isnan(x)] if x.size else x


def _mean(x, default=float("nan")):
    return float(np.mean(x)) if len(x) else default


def features(path, words=None):
    """Signal features. `words` = list of {word, s, e, syllables:[{text,s,e}]} (reference order)."""
    dur, t, f0, it, iv = _track(path)
    voiced = f0 > 0
    med = float(np.median(f0[voiced])) if voiced.any() else 120.0
    st = np.where(voiced, 12 * np.log2(np.maximum(f0, 1e-6) / med), np.nan)

    timed = [w for w in (words or []) if w.get("s") is not None]
    if timed:
        span = (timed[0]["s"], timed[-1]["e"])
    else:  # no word timings: use the loud part of the file
        loud = it[iv > (np.max(iv) - 28)] if iv.size else []
        span = (float(loud[0]), float(loud[-1])) if len(loud) else (0.0, dur)

    m = (t >= span[0]) & (t <= span[1])
    st_span = st[m]
    st_ok = st_span[~np.isnan(st_span)]
    out = {
        "duration": dur, "span": span, "median_hz": round(med, 1),
        "range_st": round(float(np.percentile(st_ok, 90) - np.percentile(st_ok, 10)), 2) if st_ok.size > 5 else None,
        "contour": [[round(float(a), 3), None if math.isnan(b) else round(float(b), 2)]
                    for a, b in zip(t[m], st_span)],
    }
    if not timed:
        return out

    wf = []
    for idx, w in enumerate(words):
        if w.get("s") is None:
            wf.append(None)
            continue
        s, e = w["s"], max(w["e"], w["s"] + 0.02)
        sy = []
        for y in w.get("syllables") or []:
            ys, ye = y["s"], max(y["e"], y["s"] + 0.02)
            sy.append({"text": y.get("text") or y.get("syl"), "dur": ye - ys,
                       "db": _mean(_win(it, iv, ys, ye), 0.0),
                       "st": _mean(_win(t, st, ys, ye))})
        wf.append({"i": idx, "word": w["word"], "s": s, "e": e, "dur": e - s,
                   "st": _mean(_win(t, st, s, e)), "st_max": float(np.nanmax(_win(t, st, s, e))) if _win(t, st, s, e).size else float("nan"),
                   "db": _mean(_win(it, iv, s, e), 0.0), "syl": sy})
    tw = [x for x in wf if x]
    pauses = []
    for a, b in zip(tw, tw[1:]):
        gap = b["s"] - a["e"]
        if gap > 0.25:
            pauses.append({"after": a["i"], "len": round(gap, 2)})
    speech_time = (tw[-1]["e"] - tw[0]["s"]) - sum(p["len"] for p in pauses)
    word_st = [x["st"] for x in tw if not math.isnan(x["st"])]
    out.update({
        "words": wf, "pauses": pauses,
        "rate_wps": round(len(tw) / speech_time, 2) if speech_time > 0 else None,
        "word_st_std": round(float(np.std(word_st)), 2) if len(word_st) > 2 else None,
    })
    return out


def _stressed(syl):
    """Index of the most prominent syllable: longer + louder + higher."""
    if len(syl) < 2:
        return None
    def z(vals):
        a = np.array(vals, dtype=float)
        a = np.where(np.isnan(a), np.nanmin(a) if not np.all(np.isnan(a)) else 0, a)
        sd = a.std()
        return (a - a.mean()) / sd if sd > 1e-6 else np.zeros_like(a)
    score = 1.0 * z([y["dur"] for y in syl]) + 1.0 * z([y["db"] for y in syl]) + 0.8 * z([y["st"] for y in syl])
    i = int(np.argmax(score))
    # require a clear winner so we don't nag about ambiguous words
    srt = sorted(score, reverse=True)
    return i if srt[0] - srt[1] > 0.6 else None


def compare(ref, you):
    """Compare your take to the reference. Returns findings + chart data."""
    res = {"findings": [], "chart": None, "stats": {}}
    # global melody
    if ref.get("range_st") and you.get("range_st"):
        res["stats"]["range"] = {"ref": ref["range_st"], "you": you["range_st"]}
        ratio = you["range_st"] / ref["range_st"]
        if ratio < 0.7:
            res["findings"].append({"kind": "melody", "sev": 3,
                "text": f"Your pitch is flatter than the reference ({you['range_st']:.1f} vs {ref['range_st']:.1f} semitones of movement). Let your voice rise and fall more on key words."})
        elif ratio > 1.45:
            res["findings"].append({"kind": "melody", "sev": 2,
                "text": f"Your pitch swings more than the reference ({you['range_st']:.1f} vs {ref['range_st']:.1f} semitones). American narration is steadier; keep the ups and downs for the important words."})

    rw, yw = ref.get("words"), you.get("words")
    if not rw or not yw:
        res["chart"] = _chart_no_words(ref, you)
        return res

    pairs = [(r, y) for r, y in zip(rw, yw) if r and y]
    if len(pairs) < 2:
        res["chart"] = _chart_no_words(ref, you)
        return res

    # rate
    if ref.get("rate_wps") and you.get("rate_wps"):
        res["stats"]["rate"] = {"ref": ref["rate_wps"], "you": you["rate_wps"]}
        rr = you["rate_wps"] / ref["rate_wps"]
        if rr < 0.8:
            res["findings"].append({"kind": "rhythm", "sev": 2, "text": f"You're speaking slower than the reference ({you['rate_wps']} vs {ref['rate_wps']} words/sec). Speed up the small words (the, of, to, and)."})
        elif rr > 1.2:
            res["findings"].append({"kind": "rhythm", "sev": 2, "text": f"You're rushing ({you['rate_wps']} vs {ref['rate_wps']} words/sec). Slow down and give the stressed words room."})

    # relative word durations (rhythm / stress-timing)
    rtot = sum(r["dur"] for r, _ in pairs)
    ytot = sum(y["dur"] for _, y in pairs)
    for r, y in pairs:
        if len(r["word"]) < 3:
            continue
        ratio = (y["dur"] / ytot) / (r["dur"] / rtot)
        if ratio > 1.7:
            res["findings"].append({"kind": "rhythm", "sev": 2, "word": r["i"],
                "text": f"You stretched “{r['word']}” — it's about {ratio:.1f}× longer than in the reference. Say it quicker and lighter."})
        elif ratio < 0.55 and r["dur"] > 0.25:
            res["findings"].append({"kind": "rhythm", "sev": 2, "word": r["i"],
                "text": f"You clipped “{r['word']}” too short. The reference leans on it; hold it longer."})

    # word stress (which syllable is prominent)
    for r, y in pairs:
        if len(r["syl"]) >= 2 and len(r["syl"]) == len(y["syl"]):
            ri, yi = _stressed(r["syl"]), _stressed(y["syl"])
            if ri is not None and yi is not None and ri != yi:
                res["findings"].append({"kind": "stress", "sev": 4, "word": r["i"],
                    "text": f"Stress in “{r['word']}”: the reference stresses “{r['syl'][ri]['text']}” (syllable {ri + 1}), you stressed “{y['syl'][yi]['text']}”."})

    # pitch peaks (focus words) + sentence ending
    r_mean = np.nanmean([r["st"] for r, _ in pairs])
    y_mean = np.nanmean([y["st"] for _, y in pairs])
    for r, y in pairs:
        if math.isnan(r["st"]) or math.isnan(y["st"]):
            continue
        if r["st"] - r_mean > 2.0 and y["st"] - y_mean < 0.5:
            res["findings"].append({"kind": "melody", "sev": 3, "word": r["i"],
                "text": f"The reference lifts its pitch on “{r['word']}” to highlight it; you kept it flat. Push that word up."})
    tail_r = [r["st"] for r, _ in pairs[-2:]]
    tail_y = [y["st"] for _, y in pairs[-2:]]
    if len(pairs) >= 3 and not any(math.isnan(v) for v in tail_r + tail_y):
        r_end = tail_r[-1] - np.nanmean([r["st"] for r, _ in pairs[:-1]])
        y_end = tail_y[-1] - np.nanmean([y["st"] for _, y in pairs[:-1]])
        if r_end < -1.5 and y_end > 0.5:
            res["findings"].append({"kind": "melody", "sev": 4, "text": "The reference falls at the end of the sentence; yours goes up. Let the last word drop to sound finished and confident."})
        elif r_end > 1.0 and y_end < -1.0:
            res["findings"].append({"kind": "melody", "sev": 3, "text": "The reference rises at the end (question/continuation); yours falls. Lift the last word."})

    # pauses
    rp = {p["after"]: p["len"] for p in ref.get("pauses", [])}
    yp = {p["after"]: p["len"] for p in you.get("pauses", [])}
    names = {r["i"]: r["word"] for r, _ in pairs}
    for i, ln in rp.items():
        if ln > 0.3 and i not in yp and i in names:
            res["findings"].append({"kind": "rhythm", "sev": 1, "word": i, "text": f"Take a short pause after “{names[i]}”, like the reference does."})
    for i, ln in yp.items():
        if ln > 0.35 and i not in rp and i in names:
            res["findings"].append({"kind": "rhythm", "sev": 2, "word": i, "text": f"You broke the flow after “{names[i]}” ({ln:.1f}s pause). Keep that phrase connected."})

    res["chart"] = _chart_aligned(ref, you, pairs)
    return res


def _chart_aligned(ref, you, pairs):
    """Warp your timeline onto the reference using word boundaries, so the curves line up."""
    anchors = []
    for r, y in pairs:
        anchors.append((y["s"], r["s"]))
        anchors.append((y["e"], r["e"]))
    anchors.sort()
    xs, ys = [], []
    for a, b in anchors:
        if (not xs or a > xs[-1]) and (not ys or b > ys[-1]):
            xs.append(a); ys.append(b)
    you_c = []
    for tt, v in you["contour"]:
        if xs[0] <= tt <= xs[-1]:
            you_c.append([round(float(np.interp(tt, xs, ys)), 3), v])
    return {"ref": ref["contour"], "you": you_c,
            "words": [[r["s"], r["e"], r["word"]] for r, _ in pairs], "aligned": True}


def _chart_no_words(ref, you):
    """Without word timings, stretch both to 0..1 of their speech span."""
    def norm(f):
        a, b = f["span"]
        L = max(b - a, 1e-3)
        return [[round((t - a) / L, 4), v] for t, v in f["contour"]]
    return {"ref": norm(ref), "you": norm(you), "words": [], "aligned": False}


def speaker_profile(feats):
    return {"rate_wps": feats.get("rate_wps"), "range_st": feats.get("range_st"),
            "word_st_std": feats.get("word_st_std"), "median_hz": feats.get("median_hz"),
            "pauses_per_100w": round(100 * len(feats.get("pauses", [])) / max(1, len([w for w in feats.get("words", []) if w])), 1),
            "mean_pause": round(float(np.mean([p["len"] for p in feats["pauses"]])), 2) if feats.get("pauses") else None}
