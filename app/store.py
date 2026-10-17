"""Simple JSON-on-disk storage for settings, projects and speakers."""
import json
import os
import threading
import uuid

DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data")))
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
SPEAKERS_DIR = os.path.join(DATA_DIR, "speakers")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
for d in (DATA_DIR, PROJECTS_DIR, SPEAKERS_DIR):
    os.makedirs(d, exist_ok=True)

_lock = threading.RLock()

DEFAULT_SETTINGS = {
    "tts_provider": "azure",           # azure | openai | elevenlabs
    "azure_key": "",
    "azure_region": "eastus",
    "azure_voice": "en-US-AndrewNeural",
    "azure_rate": "0%",                # e.g. -10% for slower narration
    "openai_key": "",
    "openai_model": "gpt-4o-mini-tts",
    "openai_voice": "onyx",
    "openai_instructions": "Speak in a clear, neutral General American accent, like a professional YouTube narrator.",
    "elevenlabs_key": "",
    "elevenlabs_voice_id": "pNInz6obpgDQGcFmaJgB",
    "elevenlabs_model": "eleven_multilingual_v2",
    "target_speaker": "",              # speaker id whose style you're matching
    "export_gap": 0.45,                # seconds of silence between sentences
    "youtube_max_minutes": 10,
}
SECRET_KEYS = ("azure_key", "openai_key", "elevenlabs_key")


def new_id():
    return uuid.uuid4().hex[:10]


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    with _lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


# ---------- settings ----------
def get_settings():
    s = dict(DEFAULT_SETTINGS)
    s.update(read_json(SETTINGS_PATH, {}) or {})
    # environment variables act as fallbacks for keys (handy on Railway)
    for k, env in (("azure_key", "AZURE_SPEECH_KEY"), ("azure_region", "AZURE_SPEECH_REGION"),
                   ("openai_key", "OPENAI_API_KEY"), ("elevenlabs_key", "ELEVENLABS_API_KEY")):
        if not s.get(k) and os.environ.get(env):
            s[k] = os.environ[env]
    return s


def public_settings():
    s = get_settings()
    out = {k: v for k, v in s.items() if k not in SECRET_KEYS}
    for k in SECRET_KEYS:
        v = s.get(k) or ""
        out[k + "_set"] = bool(v)
        out[k + "_hint"] = ("••••" + v[-4:]) if v else ""
    return out


def save_settings(patch):
    with _lock:
        current = read_json(SETTINGS_PATH, {}) or {}
        for k, v in patch.items():
            if k not in DEFAULT_SETTINGS:
                continue
            if k in SECRET_KEYS and (v is None or v == ""):
                continue  # blank = keep existing key
            if k in SECRET_KEYS and v == "__clear__":
                v = ""
            current[k] = v
        write_json(SETTINGS_PATH, current)
    return public_settings()


# ---------- projects ----------
def project_dir(pid):
    return os.path.join(PROJECTS_DIR, pid)


def project_path(pid):
    return os.path.join(project_dir(pid), "project.json")


def get_project(pid):
    return read_json(project_path(pid))


def save_project(p):
    write_json(project_path(p["id"]), p)


def list_projects():
    out = []
    for pid in os.listdir(PROJECTS_DIR):
        p = get_project(pid)
        if p:
            out.append({"id": p["id"], "title": p["title"], "created": p.get("created"),
                        "kind": p.get("kind", "script"), "n": len(p["sentences"]),
                        "done": sum(1 for s in p["sentences"] if s.get("takes"))})
    return sorted(out, key=lambda x: x.get("created") or 0, reverse=True)


# ---------- speakers ----------
def speaker_dir(sid):
    return os.path.join(SPEAKERS_DIR, sid)


def get_speaker(sid):
    return read_json(os.path.join(speaker_dir(sid), "speaker.json"))


def save_speaker(sp):
    write_json(os.path.join(speaker_dir(sp["id"]), "speaker.json"), sp)


def list_speakers():
    out = []
    for sid in os.listdir(SPEAKERS_DIR):
        sp = get_speaker(sid)
        if sp:
            out.append(sp)
    return sorted(out, key=lambda x: x.get("created") or 0, reverse=True)


def media_url(path):
    """Turn an absolute path inside DATA_DIR into a /media URL."""
    rel = os.path.relpath(path, DATA_DIR).replace(os.sep, "/")
    return "/media/" + rel
