"""Reference-voice generation. Three switchable providers: Azure (default), OpenAI, ElevenLabs."""
import os
from xml.sax.saxutils import escape

import httpx

from . import audio


class TTSError(Exception):
    pass


# Delivery energy presets — applied to every provider so the reference sounds like a real YouTube narrator.
ENERGY = {
    "calm": {"style": "Calm, measured documentary narrator. Warm and clear, unhurried.",
             "azure": {"rate": "0%", "pitch": "0%", "express": None},
             "eleven": {"stability": 0.6, "style": 0.0}},
    "engaging": {"style": "Energetic, engaging YouTube storyteller. Bright and confident with lots of vocal variety: punch the key words, build intrigue, keep a lively forward pace, and let sentence endings land firmly.",
                 "azure": {"rate": "+6%", "pitch": "+3%", "express": "cheerful"},
                 "eleven": {"stability": 0.38, "style": 0.45}},
    "hype": {"style": "High-energy, hyped YouTube narrator revealing a shocking twist. Fast, excited and punchy, with big emphasis and dramatic pitch jumps on the key words.",
             "azure": {"rate": "+12%", "pitch": "+6%", "express": "excited"},
             "eleven": {"stability": 0.28, "style": 0.7}},
    "dramatic": {"style": "Dramatic, suspenseful storyteller. Lean in, slow down on the reveals, strong contrast between quiet intensity and hard-hitting emphasis on key words.",
                 "azure": {"rate": "-4%", "pitch": "-2%", "express": "serious"},
                 "eleven": {"stability": 0.35, "style": 0.6}},
}
OLD_DEFAULT_STYLES = {"Calm, confident documentary narrator. Clear, natural pace."}
AZURE_EXPRESSIVE = {"en-US-DavisNeural", "en-US-GuyNeural", "en-US-JasonNeural", "en-US-TonyNeural",
                    "en-US-JennyNeural", "en-US-AriaNeural", "en-US-SaraNeural", "en-US-NancyNeural"}


def energy_of(s):
    return ENERGY.get(s.get("energy") or "engaging", ENERGY["engaging"])


def style_text(s, extra_key):
    extra = (s.get(extra_key) or "").strip()
    if extra in OLD_DEFAULT_STYLES:
        extra = ""
    base = energy_of(s)["style"] + " Natural General American accent."
    return f"{base} {extra}".strip()


def synthesize(text, out_wav, settings, provider=None):
    provider = provider or settings.get("tts_provider", "azure")
    if provider == "azure":
        _azure(text, out_wav, settings)
    elif provider == "openai":
        _openai(text, out_wav, settings)
    elif provider == "elevenlabs":
        _elevenlabs(text, out_wav, settings)
    elif provider == "gemini":
        _gemini(text, out_wav, settings)
    else:
        raise TTSError(f"Unknown voice provider: {provider}")
    return provider


def _azure(text, out_wav, s):
    key, region = s.get("azure_key"), s.get("azure_region")
    if not key:
        raise TTSError("Add your Azure Speech key in Settings.")
    voice = s.get("azure_voice") or "en-US-AndrewNeural"
    e = energy_of(s)["azure"]
    user_rate = (s.get("azure_rate") or "0%").strip()
    rate = e["rate"] if user_rate in ("0%", "") else user_rate
    inner = f"<prosody rate='{escape(rate)}' pitch='{escape(e['pitch'])}'>{escape(text)}</prosody>"
    if e["express"] and voice in AZURE_EXPRESSIVE:
        inner = f"<mstts:express-as style='{e['express']}'>{inner}</mstts:express-as>"
    ssml = ("<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
            "xmlns:mstts='https://www.w3.org/2001/mstts' xml:lang='en-US'>"
            f"<voice name='{escape(voice)}'>{inner}</voice></speak>")
    r = httpx.post(f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
                   headers={"Ocp-Apim-Subscription-Key": key,
                            "Content-Type": "application/ssml+xml",
                            "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
                            "User-Agent": "ShadowCoach"},
                   content=ssml.encode("utf-8"), timeout=60)
    if r.status_code != 200:
        raise TTSError(f"Azure voice error {r.status_code}: {r.text[:200]}")
    with open(out_wav, "wb") as f:
        f.write(r.content)


def _openai(text, out_wav, s):
    key = s.get("openai_key")
    if not key:
        raise TTSError("Add your OpenAI API key in Settings.")
    model = s.get("openai_model") or "gpt-4o-mini-tts"
    body = {"model": model, "voice": s.get("openai_voice") or "onyx", "input": text,
            "response_format": "wav"}
    if model.startswith("gpt-4o"):
        body["instructions"] = style_text(s, "openai_instructions")
    r = httpx.post("https://api.openai.com/v1/audio/speech",
                   headers={"Authorization": f"Bearer {key}"}, json=body, timeout=120)
    if r.status_code != 200:
        raise TTSError(f"OpenAI voice error {r.status_code}: {r.text[:200]}")
    tmp = out_wav + ".src.wav"
    with open(tmp, "wb") as f:
        f.write(r.content)
    audio.to_wav(tmp, out_wav, rate=24000)
    os.remove(tmp)


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _find_audio(node):
    """Return base64 audio data from an Interactions API response, wherever it sits."""
    if isinstance(node, dict):
        if node.get("type") == "audio" and node.get("data"):
            return node["data"]
        for k in ("output_audio", "steps", "content", "outputs"):
            if k in node:
                found = _find_audio(node[k])
                if found:
                    return found
        for v in node.values():
            found = _find_audio(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in reversed(node):
            found = _find_audio(v)
            if found:
                return found
    return None


def _gemini(text, out_wav, s):
    key = s.get("gemini_key")
    if not key:
        raise TTSError("Add your Gemini API key (from Google AI Studio) in Settings.")
    content = {"type": "text", "text": text,
               "annotations": [{"type": "speech_metadata", "style": style_text(s, "gemini_style")}]}
    body = {"model": s.get("gemini_model") or "gemini-3.8-flash-tts",
            "input": [{"type": "user_input", "content": [content]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": s.get("gemini_voice") or "Charon"}]}}
    r = httpx.post(f"{GEMINI_BASE}/interactions",
                   headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                   json=body, timeout=120)
    if r.status_code == 429:
        raise TTSError("Gemini free-tier limit reached for now. Wait a bit, or switch the reference voice to Azure.")
    if r.status_code != 200:
        raise TTSError(f"Gemini voice error {r.status_code}: {r.text[:300]}")
    data = _find_audio(r.json())
    if not data:
        raise TTSError("Gemini returned no audio for this sentence.")
    import base64
    raw = base64.b64decode(data)
    tmp = out_wav + ".src.wav"
    with open(tmp, "wb") as f:
        f.write(raw)
    if not raw.startswith(b"RIFF"):  # headerless 16-bit PCM @ 24 kHz
        audio.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1",
                   "-i", tmp, out_wav])
    else:
        audio.to_wav(tmp, out_wav, rate=24000)
    os.remove(tmp)


def gemini_voices(key, accent="American"):
    """American-accented voices from Gemini's extended voice library (falls back to prebuilt list)."""
    voices, token = [], None
    for _ in range(5):
        params = {"accent": accent, "language_code": "en-US", "page_size": 100}
        if token:
            params["page_token"] = token
        r = httpx.get(f"{GEMINI_BASE}/voices", headers={"x-goog-api-key": key}, params=params, timeout=30)
        if r.status_code != 200:
            raise TTSError(f"Couldn't list Gemini voices ({r.status_code}): {r.text[:200]}")
        j = r.json()
        for v in j.get("voices", []):
            voices.append({"id": v.get("id"), "name": v.get("display_name") or v.get("id"),
                           "gender": v.get("gender"), "accent": v.get("accent"),
                           "description": v.get("description")})
        token = j.get("next_page_token") or j.get("nextPageToken")
        if not token:
            break
    return voices


def _elevenlabs(text, out_wav, s):
    key = s.get("elevenlabs_key")
    if not key:
        raise TTSError("Add your ElevenLabs API key in Settings.")
    vid = s.get("elevenlabs_voice_id") or "pNInz6obpgDQGcFmaJgB"
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}?output_format=mp3_44100_128",
                   headers={"xi-api-key": key, "Content-Type": "application/json"},
                   json={"text": text, "model_id": s.get("elevenlabs_model") or "eleven_multilingual_v2",
                         "voice_settings": {"stability": energy_of(s)["eleven"]["stability"], "similarity_boost": 0.75,
                                            "style": energy_of(s)["eleven"]["style"], "use_speaker_boost": True}},
                   timeout=120)
    if r.status_code != 200:
        raise TTSError(f"ElevenLabs error {r.status_code}: {r.text[:200]}")
    tmp = out_wav + ".src.mp3"
    with open(tmp, "wb") as f:
        f.write(r.content)
    audio.to_wav(tmp, out_wav, rate=44100)
    os.remove(tmp)
