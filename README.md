# ShadowCoach

Sentence-by-sentence shadowing for voiceovers, with an accent check that listens to the actual sound of your voice.

## What it does

1. **Paste a script.** It's split into sentences.
2. **Hear an American reference** for each sentence — Azure (default), OpenAI or ElevenLabs, switchable any time. Slow it to 0.6×–0.9× and loop it.
3. **Record yourself** (press `R`). It plays reference → you back to back automatically.
4. **Accent check** on every take, from the audio itself:
   - **Sounds** — Azure pronunciation assessment (American English) scores every phoneme and tells you what it heard instead, e.g. “/v/ came out closer to /w/”, with a mouth-position tip.
   - **Melody** — your pitch curve is drawn over the reference, time-aligned word by word. Flags flat delivery, missed focus words, and endings that rise when they should fall.
   - **Word stress** — finds the loudest/longest/highest syllable in each word and compares it with the reference (“stress *char* in *character*”).
   - **Rhythm** — words you stretched or clipped, pauses you added or skipped, and pace.
5. **Keep your best take** (`K`) and press **Build final voiceover** to get one MP3/WAV, trimmed, evenly spaced and loudness-normalised for YouTube (−14 LUFS).

### Learn from real narrators

On the **Speakers** tab, paste a YouTube link (or upload audio). The app transcribes it with word timings, measures that narrator's pace, pitch movement and pause habits, and cuts their sentences into clips.

- **Shadow their sentences** — makes a practice session where the reference is the narrator's real voice.
- **Match my style to them** — every take is also compared against that narrator's profile.

YouTube often blocks downloads from cloud servers. If that happens, download the audio on your computer and use **Upload audio**.

## Keyboard

`Space` reference · `R` record/stop · `A` A/B compare · `K` keep take · `←` `→` previous/next

## Keys you need

| Service | Used for | Free tier |
|---|---|---|
| **Azure Speech** (required for the accent check) | accent check, speaker transcription, default reference voice | F0: 5 audio hours/month speech-to-text, 0.5M characters/month neural TTS |
| OpenAI (optional) | alternative reference voice | paid |
| ElevenLabs (optional) | alternative reference voice | limited free credits |

Create an Azure **Speech** resource (Azure portal → Create → “Speech”), choose the **F0** pricing tier, then copy **Key 1** and the **Region** (e.g. `eastus`, `centralindia`) into Settings.

## Run locally

Needs Python 3.11+ and ffmpeg.

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://localhost:8000
```

Recording needs the page on `localhost` or HTTPS (browsers block the mic otherwise). Use headphones so the reference doesn't bleed into your take.

## Deploy on Railway

1. Push this folder to a GitHub repo and create a Railway service from it — Railway picks up the `Dockerfile`.
2. Add a **Volume** mounted at `/data` (stores settings, takes and speakers).
3. Variables:
   - `APP_PASSWORD` — **set this**, otherwise anyone with the URL can use your API keys. The browser will ask for it (any username).
   - Optional: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY` (used when the Settings fields are empty).
4. Generate a domain and open it.

## Project layout

```
app/main.py          API routes, take pipeline, export
app/azure_speech.py  pronunciation assessment + word-timed transcription
app/prosody.py       pitch, stress, rhythm measurement and comparison (Praat via parselmouth)
app/feedback.py      turns measurements into coaching notes + phoneme tips
app/speakers.py      YouTube/upload → transcript → profile → clips
app/tts.py           Azure / OpenAI / ElevenLabs reference voices
app/audio.py         ffmpeg conversion and final voiceover assembly
static/              the interface (plain HTML/CSS/JS)
```
