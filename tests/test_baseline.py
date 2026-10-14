"""The reference baseline's model-free parts: extraction of every corpus format and lexical search."""

from dossier_bench.baselines.extract import extract
from dossier_bench.baselines.llm import LLMBaseline, _ymd
from dossier_bench.cases import Case


def test_extracts_every_format():
    case = Case.load("harbor-dispute")
    got = {p.suffix: extract(p.name, p.read_bytes()) for p in case.files}
    assert "liquidated damages" in got[".pdf"].text.lower() or got[".pdf"].page_count
    assert got[".docx"].text.strip()
    assert "Sheet:" in got[".xlsx"].text
    assert got[".csv"].text.strip()
    assert got[".jpg"].images and not got[".jpg"].text
    eml = extract("06_delay_notice_thread_1.eml", (case.docs_dir / "06_delay_notice_thread_1.eml").read_bytes())
    assert "Subject:" in eml.text and eml.attachments and eml.attachments[0][0].endswith(".pdf")


def test_attachments_are_keyed_and_parented():
    case = Case.load("harbor-dispute")
    b = LLMBaseline(model="none")
    files = {k: parent for k, _, parent in b._files(case)}
    assert files["survey-attachment"] == "delay-thread-1"


def test_lexical_search_ranks_the_contract_first():
    case = Case.load("harbor-dispute")
    hits = LLMBaseline(model="none").search(case, "liquidated damages cap", 5)
    assert {"contract-v1", "contract-v2"} & set(hits[:3])


def test_ymd():
    assert _ymd("2025-03-14") == (2025, 3, 14)
    assert _ymd("2025-03") == (2025, 3, None)
    assert _ymd(None) == (None, None, None)


def test_distinct_photos_are_not_duplicates(monkeypatch):
    case = Case.load("harbor-dispute")
    b = LLMBaseline(model="none")
    monkeypatch.setattr(b, "_chat", lambda *a, **k: ("{}", 0.0, 0))
    docs = b.ingest(case).documents
    assert docs["slab-photo"].duplicate_of is None
    assert docs["commissioning-log"].duplicate_of is None
    assert docs["contract-v1-copy"].duplicate_of == "contract-v1"
