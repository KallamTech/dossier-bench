"""What a system under test hands back. Implement `Adapter` for your system; everything is keyed by the
case's document keys (expected/manifest.yaml `filename_to_key`), so map your own ids to those first.

Optional data is None when the system doesn't produce it; the matching score group is skipped, not failed.
Suites are optional too: implement `transcribe` / `voice_session` only if your system has dictation / realtime
voice (`supports` decides; override it to switch a suite off per configuration).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dossier_bench.cases import Case

Ymd = tuple[int | None, int | None, int | None]


@dataclass
class Document:
    key: str                                   # case doc key, or the filename for a file outside the manifest
    stages: dict[str, str] = field(default_factory=dict)   # optional per-stage state: done | failed | pending
    text: str | None = None                    # extracted / OCR text
    visual_text: str | None = None             # what the system saw in images (captions, VLM descriptions)
    title: str | None = None
    date: Ymd = (None, None, None)             # the document's own date, partial allowed
    doc_type: str | None = None
    language: str | None = None                # ISO 639-1 or 639-2
    page_count: int | None = None
    tags: list[str] = field(default_factory=list)
    summary_short: str | None = None           # one line
    summary: str | None = None                 # key points
    summary_long: str | None = None            # full summary
    parent: str | None = None                  # key of the email this was attached to
    duplicate_of: str | None = None            # key of the document this is a recognised copy of
    family: str | None = None                  # any id shared by versions of a document / messages of a thread
    family_role: str | None = None             # base | revision | message
    family_suggestions: list[str] = field(default_factory=list)  # related keys proposed but not linked


@dataclass
class Event:
    date: Ymd
    text: str
    docs: set[str] = field(default_factory=set)   # source document keys


@dataclass
class Node:
    id: Any
    name: str
    type: str | None = None
    aliases: list[str] = field(default_factory=list)


@dataclass
class Edge:
    source: Any
    target: Any
    kind: str | None = None


@dataclass
class Graph:
    nodes: list[Node]
    edges: list[Edge]
    pending: int | None = None                 # unfinished graph work (e.g. unresolved mentions); None = not tracked


@dataclass
class Usage:
    cost_usd: float = 0.0
    tokens: int = 0
    steps: dict[str, float] = field(default_factory=dict)   # step name -> cost


@dataclass
class IngestResult:
    documents: dict[str, Document]
    chronology: list[Event] | None = None
    tracks_families: bool = True               # False = the system never groups versions / threads
    graph: Graph | None = None
    usage: dict[str, Usage] = field(default_factory=dict)          # per doc key, or any label for shared work
    latency: dict[str, dict[str, float]] = field(default_factory=dict)  # per doc key: {stage: s, ..., "total": s}
    latency_note: str | None = None            # why latency was not measured


@dataclass
class Answer:
    text: str
    sources: list[str] = field(default_factory=list)   # cited case doc keys
    urls: list[str] = field(default_factory=list)      # cited web sources
    latency_s: float | None = None
    ttft_s: float | None = None
    cost_usd: float | None = None
    terminal: str = "done"                     # done | error | timeout
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class Transcript:
    text: str
    language: str | None = None                # what the system detected, any common form (en, en-GB, english)
    latency_s: float | None = None             # request to final text; measured by the bench when None
    cost_usd: float | None = None
    terminal: str = "done"                     # done | error | timeout
    error: str | None = None


@dataclass
class VoiceTurn:
    """One spoken user turn. Stream `audio` at real-time pace (dossier_bench.audio.pcm_chunks)."""

    audio: Path
    text: str                                  # what the speaker says (reference, not for the system)
    language: str
    duration_s: float
    barge_in_after_s: float | None = None      # start speaking this long after the previous reply's first audio


@dataclass
class VoiceTurnResult:
    """Times are seconds on one monotonic clock for the whole session (any origin)."""

    reply_text: str = ""                       # transcript of the spoken reply
    user_transcript: str | None = None         # what the system heard
    sources: list[str] = field(default_factory=list)
    speech_end_s: float | None = None          # last user audio sent
    first_audio_s: float | None = None         # first reply audio received
    reply_end_s: float | None = None           # reply audio finished (or stopped)
    barge_in_s: float | None = None            # when the next turn started speaking over this reply
    terminal: str = "done"
    error: str | None = None


@dataclass
class VoiceRun:
    turns: list[VoiceTurnResult]
    cost_usd: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)


SUITE_METHODS = {"ingest": "ingest", "agent": "ask", "transcription": "transcribe", "voice": "voice_session"}


class Adapter:
    """Subclass and override. `ingest` is called once per case, `ask` once per turn."""

    name = "adapter"

    def supports(self, suite: str) -> bool:
        """Whether this system runs `suite`; unsupported suites are skipped, not failed. Default: the method is overridden."""
        method = SUITE_METHODS[suite]
        return getattr(type(self), method) is not getattr(Adapter, method)

    def transcribe(self, audio: Path, language: str | None) -> Transcript:
        """Dictation: one recorded clip in, final text out. `language` is a hint (ISO 639-1); ignore it if you detect."""
        raise NotImplementedError

    def voice_session(self, case: Case, turns: list[VoiceTurn]) -> VoiceRun:
        """Realtime voice: open one session on `case`, speak `turns` in order at real-time pace, return one result per turn."""
        raise NotImplementedError

    def ingest(self, case: Case) -> IngestResult:
        raise NotImplementedError

    def ask(self, case: Case, question: str, history: list[dict[str, str]]) -> Answer:
        """history: earlier turns of this conversation as [{"role": "user"|"assistant", "content": ...}]."""
        raise NotImplementedError

    def search(self, case: Case, query: str, k: int) -> list[str]:
        """Ranked document keys for a retrieval query. Leave unimplemented to skip the search group."""
        raise NotImplementedError
