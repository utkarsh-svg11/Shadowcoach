"""ffmpeg helpers: conversion, clipping, joining takes into a finished voiceover."""
import os
import subprocess


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + (p.stderr or "")[-600:])
    return p


def to_wav16k(src, dst=None):
    """Mono 16 kHz 16-bit PCM — the format the Azure Speech SDK wants."""
    dst = dst or os.path.splitext(src)[0] + ".16k.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "16000",
         "-sample_fmt", "s16", dst])
    return dst


def to_wav(src, dst, rate=44100, start=None, duration=None, max_seconds=None):
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    elif max_seconds:
        cmd += ["-t", str(int(max_seconds))]
    cmd += ["-ac", "1", "-ar", str(rate), "-sample_fmt", "s16", dst]
    run(cmd)
    return dst


def duration(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def join_takes(paths, out_base, gap=0.45, trim_silence=True):
    """Join takes in order with a fixed gap. Returns (mp3_path, wav_path)."""
    work = out_base + "_parts"
    os.makedirs(work, exist_ok=True)
    parts = []
    silence = os.path.join(work, "gap.wav")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "anullsrc=r=44100:cl=mono", "-t", f"{max(gap, 0.01):.3f}", "-sample_fmt", "s16", silence])
    for i, p in enumerate(paths):
        dst = os.path.join(work, f"{i:04d}.wav")
        af = []
        if trim_silence:
            # trim leading/trailing room noise so the gaps are consistent
            af.append("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05")
            af.append("areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08,areverse")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", p]
        if af:
            cmd += ["-af", ",".join(af)]
        cmd += ["-ac", "1", "-ar", "44100", "-sample_fmt", "s16", dst]
        run(cmd)
        parts.append(dst)
        if i < len(paths) - 1:
            parts.append(silence)
    lst = os.path.join(work, "list.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    wav = out_base + ".wav"
    mp3 = out_base + ".mp3"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", wav])
    # gentle loudness normalisation for YouTube (-14 LUFS)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
         "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k", mp3])
    return mp3, wav
