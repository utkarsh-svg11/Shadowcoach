import base64
import os
import re
import secrets
import shutil
import time

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audio, azure_speech, feedback, prosody, speakers, store, tts

app = FastAPI(title="ShadowCoach")
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


# ---------- optional password (set APP_PASSWORD on Railway) ----------
@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if APP_PASSWORD and request.url.path != "/healthz":
        ok = False
        h = request.headers.get("authorization", "")
        if h.startswith("Basic "):
            try:
                _, pw = base64.b64decode(h[6:]).decode().split(":", 1)
                ok = secrets.compare_digest(pw, APP_PASSWORD)
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="ShadowCoach"'})
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------- helpers ----------
ABBR = r"(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e|U\.S|U\.K|No|Inc|Ltd|Co)"


def split_sentences(script):
    script = script.replace("\r", "")
    out = []
    for para in re.split(r"\n\s*\n|\n", script):
        para = para.strip()
        if not para:
            continue
        protected = re.sub(ABBR + r"\.", lambda m: m.group(0).replace(".", "§"), para)
        parts = re.split(r"(?<=[.!?…])[\"”’)]*\s+(?=[\"“‘(]?[A-Z0-9])", protected)
        for p in parts:
            p = p.replace("§", ".").strip()
            if p:
                out.append(p)
    return out


def load_project(pid):
    p = store.get_project(pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def sentence(p, i):
    if i < 0 or i >= len(p["sentences"]):
        raise HTTPException(404, "Sentence not found")
    return p["sentences"][i]


def project_view(p):
    v = dict(p)
    v["sentences"] = []
    for s in p["sentences"]:
        sv = {k: s.get(k) for k in ("text", "ref_provider", "ref_note")}
        sv["ref_url"] = store.media_url(s["ref_path"]) + f"?v={int(s.get('ref_time', 0))}" if s.get("ref_path") else None
        sv["takes"] = [{k: t.get(k) for k in ("id", "created", "scores", "kept")} |
                       {"url": store.media_url(t["path"])} for t in s.get("takes", [])]
        v["sentences"].append(sv)
    return v


# ---------- settings ----------
@app.get("/api/settings")
def get_settings():
    return store.public_settings()


@app.post("/api/settings")
def post_settings(patch: dict):
    return store.save_settings(patch)


@app.get("/api/gemini/voices")
def gemini_voices():
    key = store.get_settings().get("gemini_key")
    if not key:
        raise HTTPException(400, "Save your Gemini API key first.")
    try:
        return tts.gemini_voices(key)
    except tts.TTSError as e:
        raise HTTPException(400, str(e))


# ---------- projects ----------
class NewProject(BaseModel):
    title: str
    script: str


@app.get("/api/projects")
def projects():
    return store.list_projects()


@app.post("/api/projects")
def create_project(body: NewProject):
    sents = split_sentences(body.script)
    if not sents:
        raise HTTPException(400, "The script is empty.")
    p = {"id": store.new_id(), "title": body.title.strip() or "Untitled script", "created": time.time(),
         "kind": "script", "sentences": [{"text": s, "takes": []} for s in sents]}
    store.save_project(p)
    return project_view(p)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return project_view(load_project(pid))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    load_project(pid)
    shutil.rmtree(store.project_dir(pid), ignore_errors=True)
    return {"ok": True}


class EditSentence(BaseModel):
    text: str


@app.post("/api/projects/{pid}/sentences/{i}/text")
def edit_sentence(pid: str, i: int, body: EditSentence):
    p = load_project(pid)
    s = sentence(p, i)
    if p.get("kind") == "speaker":
        raise HTTPException(400, "Clip sentences come from the speaker's audio and can't be edited.")
    s["text"] = body.text.strip()
    s.pop("ref_path", None); s.pop("ref_analysis_path", None)
    store.save_project(p)
    return project_view(p)


@app.post("/api/projects/{pid}/sentences/{i}/reference")
def make_reference(pid: str, i: int, provider: str | None = None):
    p = load_project(pid)
    s = sentence(p, i)
    if p.get("kind") == "speaker":
        raise HTTPException(400, "This sentence already uses the real speaker's audio.")
    settings = store.get_settings()
    refdir = os.path.join(store.project_dir(pid), "refs")
    os.makedirs(refdir, exist_ok=True)
    out = os.path.join(refdir, f"{i:04d}.wav")
    try:
        used = tts.synthesize(s["text"], out, settings, provider)
    except tts.TTSError as e:
        raise HTTPException(400, str(e))
    s["ref_path"], s["ref_provider"], s["ref_time"] = out, used, time.time()
    s.pop("ref_analysis_path", None)
    store.save_project(p)
    return project_view(p)["sentences"][i]


def _ref_analysis(p, i, s, settings):
    """Assess the reference itself once, to get its word/syllable timings. Cached."""
    cached = s.get("ref_analysis_path")
    if cached and os.path.exists(cached):
        return store.read_json(cached)
    wav16 = audio.to_wav16k(s["ref_path"], s["ref_path"] + ".16k.wav")
    a = azure_speech.assess(wav16, s["text"], settings["azure_key"], settings["azure_region"],
                            audio_seconds=audio.duration(wav16))
    path = s["ref_path"] + ".analysis.json"
    store.write_json(path, a)
    s["ref_analysis_path"] = path
    store.save_project(p)
    return a


def analyse_take(p, i, take):
    s = p["sentences"][i]
    settings = store.get_settings()
    wav16 = take["wav16"]
    secs = audio.duration(wav16)
    result = {"scores": None, "words": None, "notes": None, "chart": None, "stats": {}, "warnings": []}

    you_assess = ref_assess = None
    if settings.get("azure_key"):
        try:
            you_assess = azure_speech.assess(wav16, s["text"], settings["azure_key"], settings["azure_region"], secs)
            if s.get("ref_path"):
                ref_assess = _ref_analysis(p, i, s, settings)
        except azure_speech.AzureError as e:
            result["warnings"].append(str(e))
    else:
        result["warnings"].append("Add an Azure Speech key in Settings to get sound-by-sound scores. Showing melody only.")

    you_f = prosody.features(wav16, you_assess["words"] if you_assess else None)
    comparison = None
    if s.get("ref_path"):
        ref16 = s["ref_path"] + ".16k.wav"
        if not os.path.exists(ref16):
            audio.to_wav16k(s["ref_path"], ref16)
        ref_f = prosody.features(ref16, ref_assess["words"] if ref_assess else None)
        comparison = prosody.compare(ref_f, you_f)
        result["chart"] = comparison["chart"]
        result["stats"] = comparison["stats"]
    else:
        result["warnings"].append("No reference audio for this sentence yet, so there's nothing to compare melody against.")

    profile, pname = None, None
    if settings.get("target_speaker"):
        sp = store.get_speaker(settings["target_speaker"])
        if sp and sp.get("profile"):
            profile, pname = sp["profile"], sp.get("name") or sp.get("title")
            result["stats"]["target"] = {"name": pname, **profile}
    result["stats"]["you"] = {"rate_wps": you_f.get("rate_wps"), "range_st": you_f.get("range_st")}

    fb = feedback.build(you_assess, comparison, you_f, profile, pname)
    result["notes"] = fb
    if you_assess:
        result["scores"] = you_assess["scores"]
        result["words"] = [{"word": w["word"], "error": w["error"], "acc": w.get("acc"), "heard": w.get("heard"),
                            "phonemes": [{"ph": ph["ph"], "acc": ph["acc"], "alt": next((a for a, _ in ph["nbest"] if a and a != ph["ph"]), None)}
                                         for ph in w.get("phonemes", [])],
                            "syllables": [y.get("text") for y in w.get("syllables", [])]}
                           for w in you_assess["words"]]
    return result


@app.post("/api/projects/{pid}/sentences/{i}/takes")
def upload_take(pid: str, i: int, file: UploadFile = File(...), analyse: bool = Form(True)):
    p = load_project(pid)
    sentence(p, i)
    tid = store.new_id()
    tdir = os.path.join(store.project_dir(pid), "takes")
    os.makedirs(tdir, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1] or ".webm"
    raw = os.path.join(tdir, f"{i:04d}_{tid}{ext}")
    with open(raw, "wb") as f:
        shutil.copyfileobj(file.file, f)
    play = os.path.join(tdir, f"{i:04d}_{tid}.wav")
    audio.to_wav(raw, play, rate=44100)          # browser-friendly playback everywhere (Safari too)
    wav16 = audio.to_wav16k(play, os.path.join(tdir, f"{i:04d}_{tid}.16k.wav"))
    take = {"id": tid, "created": time.time(), "raw": raw, "path": play, "wav16": wav16, "kept": False}
    analysis = None
    if analyse:
        analysis = analyse_take(p, i, take)
        take["scores"] = analysis["scores"]
        store.write_json(os.path.join(tdir, f"{tid}.json"), analysis)
    p = load_project(pid)  # reload: reference analysis may have been cached meanwhile
    p["sentences"][i].setdefault("takes", []).append(take)
    store.save_project(p)
    return {"take": {"id": tid, "created": take["created"], "scores": take.get("scores"), "kept": False,
                     "url": store.media_url(play)}, "analysis": analysis}


@app.get("/api/projects/{pid}/takes/{tid}")
def take_analysis(pid: str, tid: str):
    a = store.read_json(os.path.join(store.project_dir(pid), "takes", f"{tid}.json"))
    if a is None:
        raise HTTPException(404, "No analysis for this take")
    return a


@app.post("/api/projects/{pid}/sentences/{i}/takes/{tid}/analyse")
def reanalyse(pid: str, i: int, tid: str):
    p = load_project(pid)
    s = sentence(p, i)
    take = next((t for t in s.get("takes", []) if t["id"] == tid), None)
    if not take:
        raise HTTPException(404, "Take not found")
    analysis = analyse_take(p, i, take)
    p = load_project(pid)
    for t in p["sentences"][i]["takes"]:
        if t["id"] == tid:
            t["scores"] = analysis["scores"]
    store.save_project(p)
    store.write_json(os.path.join(store.project_dir(pid), "takes", f"{tid}.json"), analysis)
    return analysis


@app.post("/api/projects/{pid}/sentences/{i}/takes/{tid}/keep")
def keep_take(pid: str, i: int, tid: str):
    p = load_project(pid)
    s = sentence(p, i)
    for t in s.get("takes", []):
        t["kept"] = (t["id"] == tid) and not t.get("kept")
    store.save_project(p)
    return project_view(p)["sentences"][i]


@app.delete("/api/projects/{pid}/sentences/{i}/takes/{tid}")
def delete_take(pid: str, i: int, tid: str):
    p = load_project(pid)
    s = sentence(p, i)
    keep = []
    for t in s.get("takes", []):
        if t["id"] == tid:
            for k in ("raw", "path", "wav16"):
                if t.get(k) and os.path.exists(t[k]):
                    os.remove(t[k])
        else:
            keep.append(t)
    s["takes"] = keep
    store.save_project(p)
    return project_view(p)["sentences"][i]


@app.post("/api/projects/{pid}/export")
def export(pid: str):
    p = load_project(pid)
    paths, missing = [], []
    for n, s in enumerate(p["sentences"]):
        takes = s.get("takes") or []
        t = next((t for t in takes if t.get("kept")), None) or (takes[-1] if takes else None)
        if t:
            paths.append(t["path"])
        else:
            missing.append(n + 1)
    if not paths:
        raise HTTPException(400, "Record at least one take first.")
    gap = float(store.get_settings().get("export_gap") or 0.45)
    base = os.path.join(store.project_dir(pid), "export", re.sub(r"[^A-Za-z0-9_-]+", "_", p["title"])[:60] or "voiceover")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    mp3, wav = audio.join_takes(paths, base, gap=gap)
    return {"mp3": store.media_url(mp3) + f"?v={int(time.time())}", "wav": store.media_url(wav) + f"?v={int(time.time())}",
            "missing": missing, "seconds": round(audio.duration(mp3), 1)}


# ---------- speakers ----------
class NewSpeaker(BaseModel):
    url: str
    name: str = ""


def _new_speaker(name, url=None, title=None):
    sp = {"id": store.new_id(), "name": name.strip(), "url": url, "title": title, "created": time.time(),
          "status": "queued", "status_text": "Waiting to start…"}
    store.save_speaker(sp)
    return sp


def _needs_azure():
    if not store.get_settings().get("azure_key"):
        raise HTTPException(400, "Speaker analysis needs your Azure Speech key. Add it in Settings first.")


@app.get("/api/speakers")
def list_speakers():
    return store.list_speakers()


@app.post("/api/speakers/youtube")
def add_youtube(body: NewSpeaker, bg: BackgroundTasks):
    _needs_azure()
    if not re.match(r"https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", body.url.strip()):
        raise HTTPException(400, "That doesn't look like a YouTube link.")
    sp = _new_speaker(body.name or "Speaker", url=body.url.strip())
    bg.add_task(speakers.process, sp["id"])
    return sp


@app.post("/api/speakers/upload")
def add_upload(bg: BackgroundTasks, file: UploadFile = File(...), name: str = Form("Speaker")):
    _needs_azure()
    sp = _new_speaker(name, title=file.filename)
    d = store.speaker_dir(sp["id"])
    os.makedirs(d, exist_ok=True)
    src = os.path.join(d, "upload" + (os.path.splitext(file.filename or "")[1] or ".bin"))
    with open(src, "wb") as f:
        shutil.copyfileobj(file.file, f)
    bg.add_task(speakers.process, sp["id"], src)
    return sp


@app.get("/api/speakers/{sid}")
def get_speaker(sid: str):
    sp = store.get_speaker(sid)
    if not sp:
        raise HTTPException(404, "Speaker not found")
    return sp


@app.delete("/api/speakers/{sid}")
def delete_speaker(sid: str):
    shutil.rmtree(store.speaker_dir(sid), ignore_errors=True)
    s = store.get_settings()
    if s.get("target_speaker") == sid:
        store.save_settings({"target_speaker": ""})
    return {"ok": True}


@app.post("/api/speakers/{sid}/practice")
def practice_from_speaker(sid: str):
    sp = store.get_speaker(sid)
    if not sp or not sp.get("clips"):
        raise HTTPException(400, "This speaker has no clips yet.")
    p = {"id": store.new_id(), "title": f"Shadow {sp.get('name') or 'speaker'}", "created": time.time(),
         "kind": "speaker", "speaker": sid, "sentences": []}
    for c in sp["clips"]:
        p["sentences"].append({"text": c["text"], "ref_path": c["path"], "ref_provider": "speaker",
                               "ref_note": sp.get("name"), "ref_time": time.time(), "takes": []})
    store.save_project(p)
    return project_view(p)


# ---------- static ----------
# only audio folders are exposed — never settings.json (it holds your API keys)
app.mount("/media/projects", StaticFiles(directory=store.PROJECTS_DIR), name="media_projects")
app.mount("/media/speakers", StaticFiles(directory=store.SPEAKERS_DIR), name="media_speakers")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.exception_handler(RuntimeError)
def runtime_error(_, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
