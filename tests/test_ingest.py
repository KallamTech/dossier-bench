"""Every ingestion scorer against a 'perfect' IngestResult built from the ground truth (no judge, no model)."""

from datetime import date

import pytest

from dossier_bench.adapter import Document, Edge, Event, Graph, IngestResult, Node, Usage
from dossier_bench.cases import Case, available
from dossier_bench.ingest import GROUPS, IngestScorer


def perfect(case: Case) -> IngestResult:
    exp = case.expected
    docs: dict[str, Document] = {}
    for d in exp.documents:
        if "copy_of" in d:
            continue
        dt = d.get("date")
        dt = date.fromisoformat(dt) if isinstance(dt, str) else dt
        docs[d["key"]] = Document(
            d["key"], stages={"ocr": "done", "summary": "done"}, text=f"{exp.text(d['key']) or ''}\n" + "; ".join(d.get("must_phrases") or []),
            visual_text="; ".join(d.get("vision_phrases") or []) or None, title=d.get("title"),
            date=(dt.year, dt.month, dt.day) if dt else (None, None, None), doc_type=(d.get("types") or [None])[0],
            language=d.get("language"), page_count=d.get("page_count"), summary_short="x", summary="x", summary_long="x",
            parent=d.get("attached_to"),
        )
    for fam in exp.families:
        for k, role in fam["members"].items():
            docs[k].family, docs[k].family_role = fam["group"], role
    events = [Event((e["date"].year, e["date"].month, e["date"].day) if isinstance(e["date"], date) else tuple(int(x) for x in str(e["date"]).split("-")), e["text"], set(e["docs"])) for e in exp.chronology]
    nodes = [Node(i, e["name"], e["type"], e.get("aliases", [])) for i, e in enumerate(exp.graph["entities"])]
    by_name = {n.name: n.id for n in nodes}
    edges = [Edge(by_name[r["from"]], by_name[r["to"]], r["kind"]) for r in exp.graph["relations"]]
    return IngestResult(
        documents=docs, chronology=events, graph=Graph(nodes, edges, pending=0),
        usage={k: Usage(0.01, 1000, {"summary.long": 0.01}) for k in docs}, latency={next(iter(docs)): {"ocr": 30.0, "total": 120.0}},
    )


@pytest.mark.parametrize("name", available())
def test_perfect_result_scores_high(name):
    case = Case.load(name)
    res = perfect(case)
    search = lambda q, k: next(x for x in case.expected.search if x["query"] == q)["expect"]  # noqa: E731
    items = IngestScorer(res, case.expected, None, search).run()
    assert {i.group for i in items} == set(GROUPS)
    low = [(i.item, i.group, i.quality, i.detail) for i in items if i.quality is not None and i.quality < 0.99 and i.group not in ("summaries", "titles")]
    assert not low, low
    assert all(i.skipped for i in items if i.group == "summaries")
    cost = next(i for i in items if i.item == "case.total" and i.group == "cost")
    assert cost.cost_usd > 0 and "summary.long" in cost.detail["per_stage"]
    assert next(i for i in items if i.item == "case.total" and i.group == "latency").latency_s == 120.0


def test_missing_document_and_optional_groups_skip():
    case = Case.load("harbor-dispute")
    res = perfect(case)
    res.documents.pop("tender")
    res.chronology, res.graph, res.tracks_families = None, None, False
    items = IngestScorer(res, case.expected).run(["status", "dates", "chronology", "families", "graph", "search"])
    assert next(i for i in items if i.group == "status" and i.item == "tender").quality == 0.0
    for g in ("chronology", "families", "graph", "search"):
        assert all(i.skipped for i in items if i.group == g), g


def test_duplicate_copy_is_penalised_unless_recognised():
    case = Case.load("harbor-dispute")
    res = perfect(case)
    copy = case.expected.copies[0]
    res.documents[copy["copy"]] = Document(copy["copy"])
    dedup = lambda: next(i for i in IngestScorer(res, case.expected).status() if i.item.endswith(".dedup")).quality  # noqa: E731
    assert dedup() == 0.0
    res.documents[copy["copy"]].duplicate_of = copy["of"]
    assert dedup() == 1.0
