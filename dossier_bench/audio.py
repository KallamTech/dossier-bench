"""Spoken fixtures (expected/audio.yaml) and helpers for adapters that stream audio."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import yaml


@dataclass
class Clip:
    id: str                         # memo-power, memo-power.noise
    clip: str                       # base clip id, shared by its variants
    variant: str                    # clean | noise
    path: Path
    language: str
    voice: str
    duration_s: float
    text: str
    entities: list[dict] = field(default_factory=list)


@dataclass
class TurnAudio:
    scenario: str
    turn: int
    path: Path
    language: str
    duration_s: float
    text: str


class AudioManifest:
    def __init__(self, case_dir: Path):
        p = case_dir / "expected" / "audio.yaml"
        self.exists = p.exists()
        data = yaml.safe_load(p.read_text()) if self.exists else {}
        self.version = data.get("audio_version")
        self.clips = [
            Clip(c["id"], c["clip"], c["variant"], case_dir / c["file"], c["language"], c["voice"], float(c["duration_s"]), c["text"], c.get("entities", []))
            for c in data.get("clips", [])
        ]
        self.turns = {
            (t["scenario"], int(t["turn"])): TurnAudio(t["scenario"], int(t["turn"]), case_dir / t["file"], t["language"], float(t["duration_s"]), t["text"])
            for t in data.get("voice_turns", [])
        }


def duration_s(path: Path) -> float:
    return len(pcm16(path, 16_000)) / 32_000


def pcm16(path: Path, rate: int = 24_000) -> bytes:
    """Decode any fixture to raw 16-bit little-endian mono PCM at `rate` (needs ffmpeg)."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is needed to decode audio fixtures")
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"], check=True, capture_output=True).stdout


def pcm_chunks(path: Path, rate: int = 24_000, chunk_ms: int = 100, realtime: bool = True) -> Iterator[bytes]:
    """PCM16 chunks of `chunk_ms`, paced like a live microphone when `realtime`."""
    data = pcm16(path, rate)
    step = rate * 2 * chunk_ms // 1000
    t0 = time.monotonic()
    for i, off in enumerate(range(0, len(data), step)):
        if realtime:
            delay = t0 + i * chunk_ms / 1000 - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        yield data[off : off + step]
