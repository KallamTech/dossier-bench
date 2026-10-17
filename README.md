# dossier-bench

A benchmark for AI systems that **ingest a messy case file and answer questions about it**: contracts in
two versions, scanned minutes, spreadsheets, email threads with attachments, photos, a 66-page annex, a
Spanish invoice, and a document that tries a prompt injection.

Every scored item reports three numbers: **quality** (0..1), **latency** (seconds) and **cost** (USD). It
also gets a pass/fail and a delta against a saved baseline, so a change that makes answers better but
twice as slow shows up as both.

By [Kallam](https://kallam.ai). Code: Apache-2.0. Case corpus: CC BY 4.0.

## What it measures

**Ingestion.** You hand the system the files, and it hands back what it understood (see
[Writing an adapter](#writing-an-adapter)). Each of these is a scoring group:

| group | scored against the ground truth |
|---|---|
| status | every document processed; byte-identical copy not duplicated; email attachments linked to their email |
| ocr | key phrases present in the extracted text, similarity to the reference text for scans, photo captions |
| titles | would someone working the case recognise the document from the title (LLM judge; fuzzy match without one) |
| dates | the document's own date, with partial credit for month or year |
| types | document type, language, page count |
| summaries | one-liner identifies the document; key points carry the gist; long summary carries every fact (LLM judge) |
| chronology | recall of dated case events, whether each event cites the right documents, and stray events |
| families | contract versions and email threads grouped with the right roles, and no false links |
| graph | parties and people found, not duplicated, and key relationships present |
| search | MRR and hit@k for retrieval queries |
| cost / latency | per document and per case, as reported by the system |

Groups the system doesn't produce (no graph, no search, and so on) are **skipped, not failed**.

**Agent.** Multi-turn conversations with an expected answer for each turn. The scenarios cover:
- grounded Q&A that must cite the right documents;
- follow-ups that depend on the earlier turns;
- arithmetic over figures in the documents;
- questions about scans, spreadsheets, photos and version history;
- Spanish questions over Spanish documents;
- negative questions (a clause that doesn't exist, a false premise, a fact the case doesn't contain);
- prompt injection.

Answers are graded by a pinned LLM judge against `must_facts` and `must_not_claim` lists, plus literal
`contains` / `not_contains` checks and citation checks.

**Transcription.** Dictated notes about each case, in English and Spanish, each in a clean and a noisy
(pink noise, 10 dB SNR) version. A clip is scored on:
- word and character error rate against the reference, after a documented normalisation (case,
  punctuation, currency symbols, thousands separators, ordinals);
- **critical entities**: names, amounts, dates and references (clause numbers, invoice numbers), weighted
  amounts 2, dates and references 1.5, names 1. A transcript that says "€1,280,000" instead of
  "€1,288,000" is worse than one that misspells a word, and this is where it shows;
- language detection, when the system reports one;
- speed, as a real-time factor (seconds of processing per second of audio).

A clip passes at WER ≤ 0.15, entity accuracy ≥ 0.8 and the right language.

**Voice (realtime).** Spoken conversations about the case, streamed to the system at real-time pace. Each
reply is scored like an agent turn, on the transcript of what the system said. On top of that:
- time to first audio after the user stops speaking;
- turn latency;
- for barge-in turns, whether the interrupted reply stopped within a second;
- how well the system heard the user (WER of its transcript of the user's turn).

Suites the system doesn't have (no dictation, no realtime voice) are skipped, not failed.

## The cases

| case | what | files | scenarios |
|---|---|---:|---:|
| `harbor-dispute` | construction supply dispute, English and Spanish: contract v1/v2 plus a byte-identical copy, scanned kick-off minutes, change-order register (xlsx), delay-notice email thread with a PDF attachment, site photos, a 66-page technical annex, an invoice in Spanish, minutes in Spanish (docx), an HTML email, and a vendor FAQ carrying a prompt injection | 22 | 40 |
| `brightwater-acquisition` | UK share purchase: SPA draft and executed version, disclosure letter, due-diligence report, scanned board minutes and employment-tribunal claim, data-room spreadsheets, cap-table CSV, and an earn-out email thread | 18 | 29 |
| `kite-launch` | non-legal business project, a software launch: brief, roadmap and risk register (xlsx), budget CSV, vendor quote and negotiation thread, beta survey, scanned design review, press release v1/v2, and an HTML bug report | 17 | 15 |

All parties, people and events are fictional. Every email domain is `*.example`, UK postcodes use the non-existent `ZZ` area, and company, VAT and tax numbers are placeholders.

Each case has the same layout:
- `bible.yaml`: the single source of truth for the case.
- `source/`: the text of the documents.
- `generate.py`: renders the documents, byte for byte the same on every run.
- `docs/`: the files to ingest. `docs/attachments-only/` holds files that arrive only inside an email.
- `expected/`: the ground truth.
- `agent/scenarios/`: the conversations.
- `audio/script.yaml`: the dictation clips: reference text, what the voice reads, and the critical entities.
- `agent/voice/`: the realtime voice scenarios. Most reuse a text scenario's turns and expectations.
- `audio/`: rendered speech (mono 16 kHz mp3); `expected/audio.yaml` lists every file with its sha256.

`expected/manifest.yaml` maps every filename to a stable document key, and every score is keyed by
those document keys.

```
uv run python cases/harbor-dispute/generate.py --force   # re-render; bump case_version in bible.yaml if output changes
uv run --with numpy python scripts/gen_audio.py --force  # re-render the speech (needs ffmpeg; Piper runs via uvx)
```

The speech is synthetic: Piper TTS with five voices trained on public-domain, CC0 and CC BY datasets
(`cases/voices.yaml`). It is clean studio speech, so treat absolute WER as optimistic next to real
dictation; the comparison between systems is what the suite is for.

## Quick start: the reference baseline

The baseline uses one OpenAI-compatible chat model and has no retrieval index:
- **ingest:** extracts the text of each file and makes one JSON call per document;
- **ask:** puts every document into the prompt and asks the model to cite `[doc-key]`;
- **search:** idf-weighted lexical matching;
- **transcribe:** any OpenAI-compatible `/audio/transcriptions` endpoint (`stt_model`), for example a hosted
  speech-to-text model or a local [whisper.cpp](https://github.com/ggml-org/whisper.cpp) server started with
  `--inference-path /v1/audio/transcriptions`.

It has no realtime voice, so the voice suite is skipped.

It exists so the benchmark runs end to end without any product, and to give a floor to beat.

```
uv sync --group test
cp dossier-bench.example.yaml dossier-bench.yaml   # set judge.model and adapter.options.model
export OPENAI_API_KEY=...                           # or point base_url at any compatible endpoint (vLLM, Ollama, a proxy)
uv run dossier-bench cases
uv run dossier-bench run --case harbor-dispute --label baseline       # ingest + agent, then report
uv run dossier-bench baseline save --label first
uv run dossier-bench run --case harbor-dispute --suite agent --tag core --baseline first
uv run dossier-bench run --case harbor-dispute --suite transcription --variant clean    # dictation only
```

Each run writes `runs/<id>/` containing:
- `results.json`: one record per scored item;
- `report.md`: a per-group summary, the items that regressed against the baseline, and a table of every item;
- `raw/`: every judge call and every answer.

`compare` exits non-zero when an item breaks a threshold, so you can use it as a CI gate.

## Writing an adapter

Subclass `dossier_bench.adapter.Adapter`. Work in document keys: the manifest maps filenames to keys, so
translate your own ids to keys before returning anything.

```python
from pathlib import Path

from dossier_bench.adapter import (Adapter, Answer, Document, IngestResult, Transcript, VoiceRun, VoiceTurn,
                                   VoiceTurnResult)
from dossier_bench.cases import Case


class MySystem(Adapter):
    name = "my-system v1.2"

    def ingest(self, case: Case) -> IngestResult:
        key_of = case.filename_to_key
        for path in case.files:          # upload / process these; email attachments arrive inside their .eml
            ...
        return IngestResult(documents={
            "contract-v1": Document("contract-v1", title="...", date=(2024, 11, 4), doc_type="contract",
                                    language="en", text="...", summary_short="...", summary="...", summary_long="...",
                                    family="supply-agreement", family_role="base"),
            # ...
        }, chronology=[...], graph=None)   # None = you don't build a graph; that group is skipped

    def ask(self, case: Case, question: str, history: list[dict]) -> Answer:
        reply = ...                       # history holds the earlier turns of this conversation
        return Answer(text=reply.text, sources=["contract-v1"], cost_usd=reply.cost)

    def search(self, case: Case, query: str, k: int) -> list[str]:   # optional
        return ["contract-v1", "contract-v2"]

    def transcribe(self, audio: Path, language: str | None) -> Transcript:   # optional: dictation
        r = my_stt(audio.read_bytes())
        return Transcript(text=r.text, language=r.language, cost_usd=r.cost)

    def voice_session(self, case: Case, turns: list[VoiceTurn]) -> VoiceRun:  # optional: realtime voice
        ...  # one session; stream each turn with dossier_bench.audio.pcm_chunks(turn.audio) at real-time pace,
        ...  # speak over the previous reply when turn.barge_in_after_s is set, and time everything on one clock
        return VoiceRun([VoiceTurnResult(reply_text=..., speech_end_s=..., first_audio_s=..., reply_end_s=...)])
```

Override `supports(suite)` to switch a suite off for one configuration, for example a mode of your
product that has dictation but no realtime voice.

Then run it with `uv run dossier-bench run --adapter my_package.adapter:MySystem`. Alternatively, set
`adapter.class` and `adapter.options` (the constructor's keyword arguments) in `dossier-bench.yaml`.

Field semantics are documented in [`dossier_bench/adapter.py`](dossier_bench/adapter.py). Three of them
need care:
- **`Document.parent`:** for an email attachment, the key of the email it came in.
- **`Document.duplicate_of`:** set this when your system recognised a byte-identical copy instead of
  dropping it.
- **`Document.family`:** any id shared by the versions of a document or the messages of a thread.

## Scoring notes

- **Judge.** Pin one judge model and keep it fixed across the runs you compare. Without a judge, titles
  fall back to fuzzy matching, and answer facts and long summaries are skipped (they are sentences, not phrases). The
  judge's own spend is reported separately (`judge $`) and never counted as system cost.
- **Pass mark.** An item passes at quality ≥ 0.6. An agent turn's quality is the mean of its sub-scores:
  terminal state, answer facts, rubric, contains/not_contains, citations, and any latency/cost budgets.
- **Report groups.** Agent scenarios are grouped by `group` in the report, and you can filter them with
  `--tag core`, `--group negative` or `--scenario qa-ld-rate`. The `routing` group holds questions that
  need one particular kind of source: a spreadsheet, a photo, the version history, a single page, or the web.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). New cases are welcome. Fictional parties only, with a
deterministic `generate.py` and ground truth derived from the bible.

## Licence

- Code (`dossier_bench/`, `tests/`, `cases/*/generate.py`): [Apache-2.0](LICENSE).
- Case corpus (`cases/`): [CC BY 4.0](cases/LICENSE).

Attribute as "dossier-bench by Kallam".
