"""Reference-voice generation. Three switchable providers: Azure (default), OpenAI, ElevenLabs."""
import os
from xml.sax.saxutils import escape

import httpx

from . import audio


class TTSError(Exception):
    pass


def synthesize(text, out_wav, settings, provider=None):
    provider = provider or settings.get("tts_provider", "azure")
    if provider == "azure":
        _azure(text, out_wav, settings)
    elif provider == "openai":
        _openai(text, out_wav, settings)
    elif provider == "elevenlabs":
        _elevenlabs(text, out_wav, settings)
    else:
        raise TTSError(f"Unknown voice provider: {provider}")
    return provider


def _azure(text, out_wav, s):
    key, region = s.get("azure_key"), s.get("azure_region")
    if not key:
        raise TTSError("Add your Azure Speech key in Settings.")
    voice = s.get("azure_voice") or "en-US-AndrewNeural"
    rate = s.get("azure_rate") or "0%"
    ssml = (f"<speak version='1.0' xml:lang='en-US'><voice name='{escape(voice)}'>"
            f"<prosody rate='{escape(rate)}'>{escape(text)}</prosody></voice></speak>")
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
    if model.startswith("gpt-4o") and s.get("openai_instructions"):
        body["instructions"] = s["openai_instructions"]
    r = httpx.post("https://api.openai.com/v1/audio/speech",
                   headers={"Authorization": f"Bearer {key}"}, json=body, timeout=120)
    if r.status_code != 200:
        raise TTSError(f"OpenAI voice error {r.status_code}: {r.text[:200]}")
    tmp = out_wav + ".src.wav"
    with open(tmp, "wb") as f:
        f.write(r.content)
    audio.to_wav(tmp, out_wav, rate=24000)
    os.remove(tmp)


def _elevenlabs(text, out_wav, s):
    key = s.get("elevenlabs_key")
    if not key:
        raise TTSError("Add your ElevenLabs API key in Settings.")
    vid = s.get("elevenlabs_voice_id") or "pNInz6obpgDQGcFmaJgB"
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}?output_format=mp3_44100_128",
                   headers={"xi-api-key": key, "Content-Type": "application/json"},
                   json={"text": text, "model_id": s.get("elevenlabs_model") or "eleven_multilingual_v2"},
                   timeout=120)
    if r.status_code != 200:
        raise TTSError(f"ElevenLabs error {r.status_code}: {r.text[:200]}")
    tmp = out_wav + ".src.mp3"
    with open(tmp, "wb") as f:
        f.write(r.content)
    audio.to_wav(tmp, out_wav, rate=44100)
    os.remove(tmp)
