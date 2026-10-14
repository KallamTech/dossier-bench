"""Deterministic scorers. Every function returns a float in 0..1 (or a tuple with detail)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Iterable

from rapidfuzz import fuzz


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"[^a-z0-9€%./:-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_num(s: str) -> str:
    """Make 1,288,000 / 1.288.000 / 1288000 comparable."""
    return re.sub(r"(?<=\d)[.,](?=\d{3}\b)", "", s)


def phrase_coverage(text: str, phrases: Iterable[str]) -> tuple[float, list[str]]:
    t = norm(norm_num(text))
    missing = [p for p in phrases if norm(norm_num(p)) not in t]
    phrases = list(phrases)
    if not phrases:
        return 1.0, []
    return round(1 - len(missing) / len(phrases), 3), missing


def text_similarity(a: str, b: str) -> float:
    """Character-level similarity on normalised text; robust to OCR noise."""
    return round(fuzz.ratio(norm(a), norm(b)) / 100, 3)


def date_score(expected: date | None, got: tuple[int | None, int | None, int | None], tolerance: str = "exact") -> tuple[float, str]:
    y, m, d = got
    if tolerance == "none":
        return 1.0, "undated document; any value accepted"
    if expected is None:
        return (1.0, "no expectation") if tolerance != "exact" else (1.0 if y is None else 0.0, "expected no date")
    if y is None:
        return 0.0, "no date extracted"
    if (y, m, d) == (expected.year, expected.month, expected.day):
        return 1.0, "exact"
    if (y, m) == (expected.year, expected.month):
        return 0.5, f"same month, day {d} != {expected.day}"
    if y == expected.year:
        return 0.2, f"same year only ({y}-{m}-{d})"
    return 0.0, f"got {y}-{m}-{d}, expected {expected.isoformat()}"


_LANG3 = {"eng": "en", "spa": "es", "fra": "fr", "fre": "fr", "deu": "de", "ger": "de", "ita": "it", "por": "pt", "nld": "nl", "dut": "nl", "ara": "ar", "pol": "pl", "ron": "ro", "rum": "ro"}


def lang2(code: str | None) -> str:
    """ISO 639-2/3 ('eng') or 639-1 ('en') → 639-1."""
    c = norm(code)
    return _LANG3.get(c, c[:2] if len(c) == 2 else c)


def in_set(value: str | None, allowed: Iterable[str]) -> tuple[float, str]:
    v = norm(value)
    allowed = [norm(a) for a in allowed]
    if not allowed:
        return 1.0, "no expectation"
    if v in allowed:
        return 1.0, "exact"
    if any(a in v or v in a for a in allowed if a and v):
        return 0.7, f"partial: {value!r}"
    return 0.0, f"{value!r} not in {sorted(set(allowed))}"


def set_f1(expected: Iterable[str], got: Iterable[str], match=None) -> tuple[float, dict]:
    """Precision/recall/F1 over sets with an optional fuzzy matcher match(e, g)->bool."""
    expected, got = list(expected), list(got)
    match = match or (lambda e, g: norm(e) == norm(g))
    hit_e = set()
    hit_g = set()
    for i, e in enumerate(expected):
        for j, g in enumerate(got):
            if j in hit_g:
                continue
            if match(e, g):
                hit_e.add(i)
                hit_g.add(j)
                break
    recall = len(hit_e) / len(expected) if expected else 1.0
    precision = len(hit_g) / len(got) if got else (1.0 if not expected else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return round(f1, 3), {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "missing": [expected[i] for i in range(len(expected)) if i not in hit_e],
        "unexpected": [got[j] for j in range(len(got)) if j not in hit_g][:25],
    }


def recall_only(expected: Iterable[str], got: Iterable[str], match=None) -> tuple[float, dict]:
    _, d = set_f1(expected, got, match)
    return d["recall"], d


def name_match(expected: str, got: str, aliases: Iterable[str] = (), strict: bool = False) -> bool:
    """Loose (default): substring either way or fuzzy ≥ 90 — for recall ("Tidewell" finds "Tidewell Fabrication Ltd").
    Strict: equality or fuzzy ≥ 92 against the name or an alias — for counting duplicate nodes,
    where substring matching would count "Brisamar's survey" as a copy of "Brisamar Logistics SA"."""
    g = norm(got)
    cands = [norm(expected)] + [norm(a) for a in aliases]
    if strict:
        return any(c and (c == g or fuzz.ratio(c, g) >= 92) for c in cands)
    return any(c and (c == g or c in g or g in c or fuzz.ratio(c, g) >= 90) for c in cands)


def mrr(expected: Iterable[str], ranked: list[str], k: int) -> tuple[float, dict]:
    """Reciprocal rank of the first expected item within top-k; hit@k over all expected."""
    expected = list(expected)
    top = ranked[:k]
    first = next((i for i, r in enumerate(top) if r in expected), None)
    hits = [e for e in expected if e in top]
    return (round(1 / (first + 1), 3) if first is not None else 0.0), {
        "hit_at_k": round(len(hits) / len(expected), 3) if expected else 1.0,
        "top": top,
        "missing": [e for e in expected if e not in top],
    }


def pct_increase(new: float | None, base: float | None) -> float | None:
    if new is None or base is None or base == 0:
        return None
    return round((new - base) / base * 100, 1)


def urls_note(urls: list[str]) -> str:
    """Web sources usually travel beside the reply text, not in it; show them to the judge."""
    urls = [u for u in urls if u]
    return ("\n\nSources cited:\n" + "\n".join(f"- {u}" for u in dict.fromkeys(urls))) if urls else ""
