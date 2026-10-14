"""What a system under test hands back. Implement `Adapter` for your system; everything is keyed by the
case's document keys (expected/manifest.yaml `filename_to_key`), so map your own ids to those first.

Optional data is None when the system doesn't produce it; the matching score group is skipped, not failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


class Adapter:
    """Subclass and override. `ingest` is called once per case, `ask` once per turn."""

    name = "adapter"

    def ingest(self, case: Case) -> IngestResult:
        raise NotImplementedError

    def ask(self, case: Case, question: str, history: list[dict[str, str]]) -> Answer:
        """history: earlier turns of this conversation as [{"role": "user"|"assistant", "content": ...}]."""
        raise NotImplementedError

    def search(self, case: Case, query: str, k: int) -> list[str]:
        """Ranked document keys for a retrieval query. Leave unimplemented to skip the search group."""
        raise NotImplementedError
