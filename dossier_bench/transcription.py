"""Transcription suite: every clip in expected/audio.yaml through `adapter.transcribe`, scored on word and
character error rate, critical entities, language and speed.

Normalisation (applied to reference, hypothesis and entity forms alike): NFKC + casefold; currency symbols and
currency words dropped (the amount is what matters); % → percent / por ciento, "per cent" → percent; thousands
separators removed (also when a segment break split the number: "2,400, 000") and decimal commas made points; ordinal suffixes dropped and "7th of February" → "7 february";
hyphens → spaces; other punctuation removed; whitespace collapsed.
"""

from __future__ import annotations

import re
import time
import unicodedata

from rapidfuzz.distance import Levenshtein
from rich.console import Console

from dossier_bench.adapter import Adapter, Transcript
from dossier_bench.audio import Clip
from dossier_bench.cases import Case
from dossier_bench.results import Item, Run

ENTITY_WEIGHTS = {"amount": 2.0, "date": 1.5, "ref": 1.5, "name": 1.0}
PASS_WER = 0.15
PASS_ENTITIES = 0.8

_CURRENCY_WORDS = {"en": {"euro", "euros", "pound", "pounds", "sterling", "gbp", "eur"}, "es": {"euro", "euros", "libra", "libras", "esterlinas"}}
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_LANGS = {"english": "en", "spanish": "es", "castellano": "es", "espanol": "es", "español": "es", "french": "fr", "german": "de", "italian": "it", "portuguese": "pt"}


def normalize(text: str, language: str = "en") -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).casefold()
    s = s.replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"[€£$¥]", " ", s)
    s = re.sub(r"\bper cent\b", "percent", s)
    s = s.replace("%", " por ciento " if language == "es" else " percent ")
    s = re.sub(r"(?<=\d)([.,]) (?=\d{3}\b)", r"\1", s)   # "2,400, 000": a segment break inside a number
    s = re.sub(r"(?<=\d) (?=\d{3}\b)", "", s)              # "180 000": space as thousands separator
    s = re.sub(r"(?<=\d) \.(?=\d)", ".", s)                # "3 .0"
    s = re.sub(r"(?<=\d)[.,](?=\d{3}\b)", "", s)          # 1,288,000 / 257.600
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)                 # 0,5 -> 0.5
    s = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", s)
    s = re.sub(rf"\b(\d+) of ({_MONTHS})\b", r"\1 \2", s)
    s = re.sub(r"(?<=\D)-|-(?=\D)", " ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)
    s = re.sub(r"[^\w.\s]", " ", s)
    words = [w for w in s.split() if w not in _CURRENCY_WORDS.get(language, set())]
    return " ".join(words)


def language_code(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip().lower()
    return _LANGS.get(v, v.split("-")[0].split("_")[0])


def wer(reference: str, hypothesis: str, language: str = "en") -> tuple[float, int, int]:
    """(rate, word edits, reference words)."""
    r, h = normalize(reference, language).split(), normalize(hypothesis, language).split()
    edits = Levenshtein.distance(r, h)
    return (round(edits / len(r), 4) if r else 0.0), edits, len(r)


def cer(reference: str, hypothesis: str, language: str = "en") -> tuple[float, int, int]:
    r, h = normalize(reference, language), normalize(hypothesis, language)
    edits = Levenshtein.distance(r, h)
    return (round(edits / len(r), 4) if r else 0.0), edits, len(r)


def entity_accuracy(entities: list[dict], hypothesis: str, language: str = "en") -> tuple[float, list[dict]]:
    """Weighted share of entities whose text (or any accepted `match` form) appears in the hypothesis."""
    hyp = f" {normalize(hypothesis, language)} "
    found, total, detail = 0.0, 0.0, []
    for e in entities:
        forms = [e["text"], *e.get("match", [])]
        ok = any(f" {normalize(f, language)} " in hyp for f in forms if normalize(f, language))
        w = ENTITY_WEIGHTS.get(e.get("kind", "name"), 1.0)
        total += w
        found += w if ok else 0.0
        detail.append({"kind": e.get("kind", "name"), "text": e["text"], "found": ok})
    return (round(found / total, 4) if total else 1.0), detail


def score_clip(clip: Clip, t: Transcript) -> tuple[float, bool, dict]:
    w, w_edits, w_ref = wer(clip.text, t.text, clip.language)
    c, c_edits, c_ref = cer(clip.text, t.text, clip.language)
    ent, ents = entity_accuracy(clip.entities, t.text, clip.language)
    det = {
        "wer": w, "cer": c, "entity_accuracy": ent, "word_edits": w_edits, "ref_words": w_ref, "char_edits": c_edits, "ref_chars": c_ref,
        "entities": ents, "missing": [e["text"] for e in ents if not e["found"]], "hypothesis": t.text,
        "language": clip.language, "variant": clip.variant, "duration_s": clip.duration_s,
    }
    subs = {"words": max(0.0, 1 - w), "entities": ent}
    got = language_code(t.language)
    if got:
        det["language_detected"] = got
        subs["language"] = 1.0 if got == clip.language else 0.0
    if t.latency_s is not None and clip.duration_s:
        det["rtf"] = round(t.latency_s / clip.duration_s, 3)
    q = round(sum(subs.values()) / len(subs), 3)
    det["subscores"] = subs
    return q, (w <= PASS_WER and ent >= PASS_ENTITIES and subs.get("language", 1.0) == 1.0), det


class TranscriptionRunner:
    def __init__(self, adapter: Adapter, case: Case, run: Run, console: Console | None = None):
        self.adapter, self.case, self.run = adapter, case, run
        self.console = console or Console()

    def run_all(self, variants: list[str] | None = None, clips: list[str] | None = None) -> list[Item]:
        items = []
        for clip in self.case.audio.clips:
            if (variants and clip.variant not in variants) or (clips and clip.clip not in clips and clip.id not in clips):
                continue
            t0 = time.monotonic()
            try:
                t = self.adapter.transcribe(clip.path, clip.language)
            except Exception as e:  # noqa: BLE001
                t = Transcript("", terminal="error", error=str(e)[:500])
            if t.latency_s is None:
                t.latency_s = round(time.monotonic() - t0, 3)
            group = f"{clip.language}-{clip.variant}"
            if t.terminal != "done":
                item = Item("transcription", group, clip.id, quality=0.0, latency_s=t.latency_s, cost_usd=t.cost_usd, passed=False, detail={"error": t.error or t.terminal})
            else:
                q, passed, det = score_clip(clip, t)
                item = Item("transcription", group, clip.id, quality=q, latency_s=t.latency_s, cost_usd=t.cost_usd, passed=passed, detail=det)
            items.append(self.run.add(item))
            self.run.write_raw(f"transcription/{clip.id}.json", {"clip": clip.id, "reference": clip.text, "transcript": t.__dict__})
            self.console.log(f"  {clip.id}: q={item.quality} wer={item.detail.get('wer')} {t.latency_s}s")
            self.run.save()
        return items
