import json

from dossier_bench.config import Config
from dossier_bench.report import aggregate, compare, load_baseline, render_markdown
from dossier_bench.results import Item, Run


def _run(tmp_path, items):
    r = Run.__new__(Run)
    r.run_id, r.case, r.dir, r.raw = "t1", "harbor-dispute", tmp_path, tmp_path / "raw"
    r.items, r.meta, r.state = items, {"started_at": "2026-10-12T00:00:00", "adapter": "test"}, {"case_id": "c", "case_version": 1}
    return r


def test_aggregate_and_compare_thresholds(tmp_path):
    cfg = Config({})
    items = [
        Item("ingestion", "dates", "a", quality=1.0, passed=True),
        Item("ingestion", "dates", "b", quality=0.5, passed=False),
        Item("ingestion", "cost", "case.total", cost_usd=1.2, passed=True),
        Item("agent", "qa", "x#t1", quality=0.9, latency_s=10, cost_usd=0.05, passed=True),
        Item("agent", "qa", "skip", skipped="requires x"),
    ]
    run = _run(tmp_path, items)
    agg = aggregate(items)
    assert agg["ingestion/dates"]["quality"] == 0.75 and agg["ingestion/dates"]["pass_rate"] == 0.5
    assert agg["ingestion/cost"]["cost_usd"] == 1.2
    base_items = [
        Item("ingestion", "dates", "a", quality=1.0), Item("ingestion", "dates", "b", quality=0.9),
        Item("ingestion", "cost", "case.total", cost_usd=0.9), Item("agent", "qa", "x#t1", quality=0.9, latency_s=5, cost_usd=0.05),
    ]
    bpath = tmp_path / "base.json"
    bpath.write_text(json.dumps({"items": [i.__dict__ for i in base_items]}))
    base = load_baseline(bpath)
    rows, violations = compare(run, base, cfg)
    keys = {v["key"]: v["flags"] for v in violations}
    assert "ingestion/dates/b" in keys and any("dropped" in f for f in keys["ingestion/dates/b"])
    assert "ingestion/cost/case.total" in keys and any("cost +33%" in f for f in keys["ingestion/cost/case.total"])
    assert "agent/qa/x#t1" in keys and any("latency +100%" in f for f in keys["agent/qa/x#t1"])
    md = render_markdown(run, cfg, base, "base")
    assert "## Regressions" in md and "ingestion/dates/b" in md and "skip" in md


def test_render_without_baseline(tmp_path):
    cfg = Config({})
    run = _run(tmp_path, [Item("ingestion", "titles", "a", quality=0.8, passed=True, detail={"why": "ok"})])
    md = render_markdown(run, cfg)
    assert "| ingestion/titles | 1 | 0.80 |" in md and "Regressions" not in md
