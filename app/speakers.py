"""Reference speakers from YouTube links or uploaded audio: download, transcribe,
profile their delivery, and cut their sentences into shadowing clips."""
import os
import time
import traceback

from . import audio, azure_speech, prosody, store


def download_youtube(url, out_dir):
    import yt_dlp
    opts = {"format": "bestaudio/best", "outtmpl": os.path.join(out_dir, "source.%(ext)s"),
            "quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    if not os.path.exists(path):  # extension can change after post-processing
        cands = [f for f in os.listdir(out_dir) if f.startswith("source.")]
        if not cands:
            raise RuntimeError("Download produced no audio file.")
        path = os.path.join(out_dir, cands[0])
    return path, info.get("title") or "YouTube video"


def _set(sp, **kw):
    sp.update(kw)
    store.save_speaker(sp)


def process(sid, source_path=None):
    """Background job. Status: downloading → transcribing → analysing → ready | error."""
    sp = store.get_speaker(sid)
    d = store.speaker_dir(sid)
    s = store.get_settings()
    try:
        if source_path is None:
            _set(sp, status="downloading", status_text="Downloading audio from YouTube…")
            source_path, title = download_youtube(sp["url"], d)
            if not sp.get("title"):
                sp["title"] = title
        max_s = int(float(s.get("youtube_max_minutes") or 10) * 60)
        hq = audio.to_wav(source_path, os.path.join(d, "source_hq.wav"), rate=44100, max_seconds=max_s)
        lo = audio.to_wav16k(hq, os.path.join(d, "source_16k.wav"))
        secs = audio.duration(lo)

        _set(sp, status="transcribing", status_text=f"Listening to {secs / 60:.1f} min of speech…")
        segs = azure_speech.transcribe(lo, s["azure_key"], s["azure_region"], audio_seconds=secs)
        if not segs:
            raise RuntimeError("No speech was recognised in this audio.")

        _set(sp, status="analysing", status_text="Measuring pace, melody and pauses…")
        words = [{"word": w["word"], "s": w["s"], "e": w["e"], "syllables": []}
                 for seg in segs for w in seg["words"]]
        feats = prosody.features(lo, words)
        profile = prosody.speaker_profile(feats)

        clips_dir = os.path.join(d, "clips")
        os.makedirs(clips_dir, exist_ok=True)
        clips = []
        for i, seg in enumerate(segs):
            length = seg["e"] - seg["s"]
            n_words = len(seg["words"])
            if length < 1.5 or length > 16 or n_words < 4:
                continue
            path = os.path.join(clips_dir, f"{i:04d}.wav")
            audio.to_wav(hq, path, rate=44100, start=max(0, seg["s"] - 0.12), duration=length + 0.3)
            clips.append({"i": i, "text": seg["text"], "s": round(seg["s"], 2), "e": round(seg["e"], 2),
                          "path": path, "url": store.media_url(path)})
        _set(sp, status="ready", status_text="", profile=profile, clips=clips,
             minutes=round(secs / 60, 1), processed=time.time())
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        msg = str(e)
        if "Sign in to confirm" in msg or "HTTP Error 403" in msg or "bot" in msg.lower():
            msg = ("YouTube blocked the download from this server. Download the audio on your own computer "
                   "and use “Upload audio” instead. (" + msg[:160] + ")")
        _set(sp, status="error", status_text=msg[:500])
