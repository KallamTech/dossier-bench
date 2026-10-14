"""Render the spoken fixtures: cases/<case>/audio/script.yaml + agent/voice/*.yaml -> mp3 files + expected/audio.yaml.

    uv run --with numpy python scripts/gen_audio.py [--case harbor-dispute] [--force]

Needs ffmpeg on PATH and Piper (`uvx --from piper-tts==1.8.0 piper`, or set PIPER to a piper binary). Voice
models are downloaded once to ~/.cache/dossier-bench/piper. Piper runs with noise_scale 0, the noise variant
uses a seeded generator and the mp3 encoder runs bit-exact, so re-rendering reproduces the committed files.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "cases"
CACHE = Path.home() / ".cache" / "dossier-bench" / "piper"
VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
RATE = 16_000
BITRATE = "32k"
NOISE_SNR_DB = 10.0
PIPER = shlex.split(os.environ.get("PIPER", "uvx --from piper-tts==1.8.0 piper"))


def voice_model(spec: dict) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    name = spec["piper"].rsplit("/", 1)[1]
    for suffix in (".onnx", ".onnx.json"):
        p = CACHE / f"{name}{suffix}"
        if not p.exists():
            print(f"downloading {name}{suffix}", file=sys.stderr)
            urllib.request.urlretrieve(f"{VOICES_URL}/{spec['piper']}{suffix}", p)
    return CACHE / f"{name}.onnx"


def synthesize(text: str, spec: dict) -> np.ndarray:
    """Piper -> float32 mono at RATE."""
    with tempfile.TemporaryDirectory() as td:
        src, raw = Path(td) / "in.txt", Path(td) / "out.wav"
        src.write_text(" ".join(text.split()))
        cmd = [*PIPER, "-m", str(voice_model(spec)), "-i", str(src), "-f", str(raw), "--noise-scale", "0", "--noise-w-scale", "0"]
        if "speaker" in spec:
            meta = yaml.safe_load((CACHE / f"{spec['piper'].rsplit('/', 1)[1]}.onnx.json").read_text())
            cmd += ["-s", str(meta["speaker_id_map"][spec["speaker"]])]
        subprocess.run(cmd, check=True, capture_output=True)
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(raw), "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768


def pink_noise(n: int, seed: int) -> np.ndarray:
    white = np.random.default_rng(seed).standard_normal(n)
    spec = np.fft.rfft(white)
    f = np.arange(len(spec), dtype=np.float64)
    f[0] = 1
    pink = np.fft.irfft(spec / np.sqrt(f), n)
    return (pink / np.sqrt(np.mean(pink**2))).astype(np.float32)


def add_noise(x: np.ndarray, seed: int, snr_db: float = NOISE_SNR_DB) -> np.ndarray:
    speech_rms = np.sqrt(np.mean(x[np.abs(x) > 1e-3] ** 2))
    noisy = x + pink_noise(len(x), seed) * speech_rms / (10 ** (snr_db / 20))
    peak = np.max(np.abs(noisy))
    return noisy / peak * 0.9 if peak > 0.99 else noisy


def write_mp3(x: np.ndarray, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "wav", "-i", "-", "-ac", "1", "-ar", str(RATE), "-b:a", BITRATE,
         "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact", str(out)],
        input=buf.getvalue(), check=True,
    )


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def seed_of(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


def text_scenarios(case_dir: Path) -> dict[str, dict]:
    out = {}
    for p in sorted((case_dir / "agent" / "scenarios").glob("*.yaml")):
        for d in yaml.safe_load(p.read_text()) or []:
            out[d["id"]] = d
    return out


def render_case(case_dir: Path, voices: dict, force: bool) -> dict:
    script = yaml.safe_load((case_dir / "audio" / "script.yaml").read_text())
    clips_out, turns_out = [], []

    def render(text: str, voice: str, rel: str, noise_seed: int | None = None) -> tuple[Path, float]:
        out = case_dir / rel
        if force or not out.exists():
            x = synthesize(text, voices[voice])
            if noise_seed is not None:
                x = add_noise(x, noise_seed)
            write_mp3(x, out)
            print(f"  {rel}", file=sys.stderr)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)], check=True, capture_output=True, text=True).stdout)
        return out, round(dur, 2)

    for c in script["clips"]:
        spoken = c.get("say") or c["text"]
        for variant in ("clean", "noise"):
            rel = f"audio/clips/{c['id']}{'' if variant == 'clean' else '.noise'}.mp3"
            out, dur = render(spoken, c["voice"], rel, seed_of(f"{case_dir.name}/{c['id']}") if variant == "noise" else None)
            clips_out.append({
                "id": c["id"] if variant == "clean" else f"{c['id']}.noise", "clip": c["id"], "variant": variant,
                **({"snr_db": NOISE_SNR_DB} if variant == "noise" else {}),
                "file": rel, "language": c["language"], "voice": c["voice"], "duration_s": dur, "sha256": sha256(out),
                "text": " ".join(c["text"].split()), "entities": c.get("entities", []),
            })

    texts = text_scenarios(case_dir)
    for p in sorted((case_dir / "agent" / "voice").glob("*.yaml")):
        for sc in yaml.safe_load(p.read_text()) or []:
            turns = texts[sc["from"]]["turns"] if sc.get("from") else sc["turns"]
            say = sc.get("say") or []
            for ti, t in enumerate(turns, start=1):
                spoken = say[ti - 1] if ti <= len(say) and say[ti - 1] else t["content"]
                rel = f"audio/voice/{sc['id']}-t{ti}.mp3"
                out, dur = render(spoken, sc["voice"], rel)
                turns_out.append({"scenario": sc["id"], "turn": ti, "file": rel, "language": sc.get("language") or sc["voice"].split("-")[0], "voice": sc["voice"], "duration_s": dur, "sha256": sha256(out), "text": t["content"]})

    manifest = {
        "audio_version": script.get("audio_version", 1),
        "generator": {"tts": "piper-tts 1.8.0 (noise_scale 0)", "sample_rate": RATE, "format": f"mp3 {BITRATE} mono", "noise": f"pink, {NOISE_SNR_DB:g} dB SNR"},
        "clips": clips_out,
        "voice_turns": turns_out,
    }
    (case_dir / "expected" / "audio.yaml").write_text(
        "# Generated by scripts/gen_audio.py from audio/script.yaml and agent/voice/*.yaml. Do not edit.\n"
        + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=120)
    )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", help="case name (repeatable); default: every case with audio/script.yaml")
    ap.add_argument("--force", action="store_true", help="re-render files that already exist")
    args = ap.parse_args()
    voices = yaml.safe_load((CASES / "voices.yaml").read_text())
    names = args.case or sorted(p.parent.parent.name for p in CASES.glob("*/audio/script.yaml"))
    for name in names:
        print(name, file=sys.stderr)
        m = render_case(CASES / name, voices, args.force)
        total = sum(c["duration_s"] for c in m["clips"]) + sum(t["duration_s"] for t in m["voice_turns"])
        print(f"  {len(m['clips'])} clips, {len(m['voice_turns'])} voice turns, {total:.0f}s audio", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
